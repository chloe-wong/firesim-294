#include <algorithm>
#include <arpa/inet.h>
#include <cstdlib>
#include <functional>
#include <omp.h>
#include <queue>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

//#define CAPTURE
#define IGNORE_PRINTF

#ifdef IGNORE_PRINTF
#define printf(fmt, ...) (0)
#endif

// param: link latency in cycles
// assuming 3.2 GHz, this number / 3.2 = link latency in ns
// e.g. setting this to 35000 gives you 35000/3.2 = 10937.5 ns latency
// IMPORTANT: this must be a multiple of 7
//
// THIS IS SET BY A COMMAND LINE ARGUMENT. DO NOT CHANGE IT HERE.
//#define LINKLATENCY 6405
int LINKLATENCY = 0;

// param: switching latency in cycles
// assuming 3.2 GHz, this number / 3.2 = switching latency in ns
//
// THIS IS SET BY A COMMAND LINE ARGUMENT. DO NOT CHANGE IT HERE.
int switchlat = 0;

#define SWITCHLATENCY (switchlat)

// param: numerator and denominator of bandwidth throttle
// Used to throttle outbound bandwidth from port
//
// THESE ARE SET BY A COMMAND LINE ARGUMENT. DO NOT CHANGE IT HERE.
int throttle_numer = 1;
int throttle_denom = 1;

// param: output buffer size and drop behavior
// Used to model finite egress buffers with tail-drop congestion management
//
// THESE ARE SET BY A COMMAND LINE ARGUMENT. DO NOT CHANGE IT HERE.
// IMPORTANT: Only enable buffer drops when running with a full OS (Linux/FreeBSD)
//            that has a TCP/IP stack to handle retransmissions!
//            For bare-metal simulations, keep buffer_drop_enabled = 0.
long output_buffer_size = 131072L;  // size in flits (default: ~8MB at 64B/flit)
int buffer_drop_enabled = 0;        // 0 = disabled (safe for bare-metal)
                                     // 1 = enabled (requires TCP/IP stack)

// param: ECMP routing mode
// Controls how multiple equal-cost paths are selected
//
// THIS IS SET BY A COMMAND LINE ARGUMENT. DO NOT CHANGE IT HERE.
int ecmp_mode = ECMP_RANDOM;        // 0 = random (original)
                                     // 1 = hash-based flow-aware ECMP

// pull in # clients config
#define NUMCLIENTSCONFIG
#include "switchconfig.h"
#undef NUMCLIENTSCONFIG

// DO NOT TOUCH
#define NUM_TOKENS (LINKLATENCY)
#define TOKENS_PER_BIGTOKEN (7)
#define BIGTOKEN_BYTES (64)
#define NUM_BIGTOKENS (NUM_TOKENS / TOKENS_PER_BIGTOKEN)
#define BUFSIZE_BYTES (NUM_BIGTOKENS * BIGTOKEN_BYTES)

// DO NOT TOUCH
#define SWITCHLAT_NUM_TOKENS (SWITCHLATENCY)
#define SWITCHLAT_NUM_BIGTOKENS (SWITCHLAT_NUM_TOKENS / TOKENS_PER_BIGTOKEN)
#define SWITCHLAT_BUFSIZE_BYTES (SWITCHLAT_NUM_BIGTOKENS * BIGTOKEN_BYTES)

uint64_t this_iter_cycles_start = 0;

// pull in mac2port array
#define MACPORTSCONFIG
#include "switchconfig.h"
#undef MACPORTSCONFIG

#include "baseport.h"
#include "flit.h"
#include "shmemport.h"
#include "socketport.h"
#include "sshport.h"

// TODO: replace these port mapping hacks with a mac -> port mapping,
// could be hardcoded

BasePort *ports[NUMPORTS];

static FILE *capture;

/* switch from input ports to output ports */
void do_fast_switching() {
#pragma omp parallel for
  for (int port = 0; port < NUMPORTS; port++) {
    ports[port]->setup_send_buf();
  }

// preprocess from raw input port to packets
#pragma omp parallel for
  for (int port = 0; port < NUMPORTS; port++) {
    BasePort *current_port = ports[port];
    uint8_t *input_port_buf = current_port->current_input_buf;

    for (int tokenno = 0; tokenno < NUM_TOKENS; tokenno++) {
      if (is_valid_flit(input_port_buf, tokenno)) {
        uint64_t flit = get_flit(input_port_buf, tokenno);

        switchpacket *sp;
        if (!(current_port->input_in_progress)) {
          sp = (switchpacket *)calloc(sizeof(switchpacket), 1);
          current_port->input_in_progress = sp;

          // here is where we inject switching latency. this is min port-to-port
          // latency
          sp->timestamp = this_iter_cycles_start + tokenno + SWITCHLATENCY;
          sp->sender = port;
        }
        sp = current_port->input_in_progress;

        sp->dat[sp->amtwritten++] = flit;
        if (is_last_flit(input_port_buf, tokenno)) {
          current_port->input_in_progress = NULL;
          if (current_port->push_input(sp)) {
            printf("packet timestamp: %ld, len: %ld, sender: %d\n",
                   this_iter_cycles_start + tokenno,
                   sp->amtwritten,
                   port);
          }
        }
      }
    }
  }

  // next do the switching. but this switching is just shuffling pointers,
  // so it should be fast. it has to be serial though...

  // NO PARALLEL!
  // shift pointers to output queues, but in order. basically.
  // until the input queues have no more complete packets
  // 1) find the next switchpacket with the lowest timestamp across all the
  // inputports 2) look at its mac, copy it into the right ports
  //          i) if it's a broadcast: sorry, you have to make N-1 copies of
  //          it... to put into the other queues

  struct tspacket {
    uint64_t timestamp;
    switchpacket *switchpack;

    bool operator<(const tspacket &o) const { return timestamp > o.timestamp; }
  };

  typedef struct tspacket tspacket;

  // TODO thread safe priority queue? could do in parallel?
  std::priority_queue<tspacket> pqueue;

  for (int i = 0; i < NUMPORTS; i++) {
    while (!(ports[i]->inputqueue.empty())) {
      switchpacket *sp = ports[i]->inputqueue.front();
      ports[i]->inputqueue.pop();
      pqueue.push(tspacket{sp->timestamp, sp});

#ifdef CAPTURE
      fputs("000", capture);
      for (int k = 0; k < sp->amtwritten; k++) {
        uint64_t flit = sp->dat[k];
        for (int j = (k > 0 ? 0 : 16); j < 64; j += 8) {
          fprintf(capture, " %02x", (flit >> j) & 0xff);
        }
      }
      fputc('\n', capture);
      fflush(capture);
#endif
    }
  }

  // next, put back into individual output queues
  while (!pqueue.empty()) {
    switchpacket *tsp = pqueue.top().switchpack;
    pqueue.pop();
    uint16_t send_to_port =
        get_port_from_flit(tsp->dat[0], 0 /* junk remove arg */);
    
    // If port requires uplink selection (multiple paths), apply ECMP
    if (send_to_port == NUMDOWNLINKS && NUMUPLINKS > 1) {
      send_to_port = select_uplink_port_ecmp(tsp->dat, tsp->amtwritten);
    }
    
    // printf("packet for port: %x\n", send_to_port);
    // printf("packet timestamp: %ld\n", tsp->timestamp);
    if (send_to_port == BROADCAST_ADJUSTED) {
#define ADDUPLINK (NUMUPLINKS > 0 ? 1 : 0)
      // this will only send broadcasts to the first (zeroeth) uplink.
      // on a switch receiving broadcast packet from an uplink, this should
      // automatically prevent switch from sending the broadcast to any uplink
      for (int i = 0; i < NUMDOWNLINKS + ADDUPLINK; i++) {
        if (i != tsp->sender) {
          switchpacket *tsp2 = (switchpacket *)malloc(sizeof(switchpacket));
          memcpy(tsp2, tsp, sizeof(switchpacket));
          ports[i]->outputqueue.push(tsp2);
        }
      }
      free(tsp);
    } else {
      ports[send_to_port]->outputqueue.push(tsp);
    }
  }

  // finally in parallel, flush whatever we can to the output queues based on
  // timestamp

#pragma omp parallel for
  for (int port = 0; port < NUMPORTS; port++) {
    BasePort *thisport = ports[port];
    thisport->write_flits_to_output();
  }
}

static void simplify_frac(int n, int d, int *nn, int *dd) {
  int a = n, b = d;

  // compute GCD
  while (b > 0) {
    int t = b;
    b = a % b;
    a = t;
  }

  *nn = n / a;
  *dd = d / a;
}

int main(int argc, char *argv[]) {
  int bandwidth;

  if (argc < 4) {
    // if insufficient args, error out
    fprintf(stdout, "usage: ./switch LINKLATENCY SWITCHLATENCY BANDWIDTH [BUFFER_DROPS [BUFFER_SIZE [FAIL_PORT [FAIL_START [FAIL_DURATION [ECMP_MODE]]]]]]\n");
    fprintf(stdout, "insufficient args provided\n.");
    fprintf(stdout,
            "LINKLATENCY and SWITCHLATENCY should be provided in cycles.\n");
    fprintf(stdout, "BANDWIDTH should be provided in Gbps\n");
    fprintf(stdout, "BUFFER_DROPS: optional, 0=disabled (default, safe for bare-metal), 1=enabled (requires OS)\n");
    fprintf(stdout, "BUFFER_SIZE: optional, egress buffer size in flits (default: 131072)\n");
    fprintf(stdout, "FAIL_PORT: optional, port number to fail (-1=none, default)\n");
    fprintf(stdout, "FAIL_START: optional, cycle to start link failure (default: 0)\n");
    fprintf(stdout, "FAIL_DURATION: optional, failure duration in cycles (0=permanent, default: 0)\n");
    fprintf(stdout, "ECMP_MODE: optional, 0=random (default), 1=hash-based flow-aware ECMP\n");
    exit(1);
  }

  LINKLATENCY = atoi(argv[1]);
  switchlat = atoi(argv[2]);
  bandwidth = atoi(argv[3]);

  // Optional: Enable buffer drops (only safe with full OS/TCP stack)
  if (argc >= 5) {
    buffer_drop_enabled = atoi(argv[4]);
  }

  // Optional: Configure buffer size
  if (argc >= 6) {
    output_buffer_size = atol(argv[5]);
  }

  // Optional: Link failure simulation
  int link_fail_port = -1;
  uint64_t link_fail_start = 0;
  uint64_t link_fail_duration = 0;
  
  if (argc >= 7) {
    link_fail_port = atoi(argv[6]);
  }
  if (argc >= 8) {
    link_fail_start = atol(argv[7]);
  }
  if (argc >= 9) {
    link_fail_duration = atol(argv[8]);
  }

  // Optional: ECMP mode
  if (argc >= 10) {
    ecmp_mode = atoi(argv[9]);
  }

  simplify_frac(bandwidth, 200, &throttle_numer, &throttle_denom);

  fprintf(stdout, "Using link latency: %d\n", LINKLATENCY);
  fprintf(stdout, "Using switching latency: %d\n", SWITCHLATENCY);
  fprintf(stdout, "BW throttle set to %d/%d\n", throttle_numer, throttle_denom);
  fprintf(stdout, "Buffer drop policy: %s\n", buffer_drop_enabled ? "ENABLED (requires OS)" : "DISABLED (safe for bare-metal)");
  if (buffer_drop_enabled) {
    fprintf(stdout, "Egress buffer size: %ld flits (~%ld KB)\n", 
            output_buffer_size, (output_buffer_size * 64) / 1024);
  }
  
  if (link_fail_port >= 0) {
    fprintf(stdout, "Link failure configured: port=%d, start_cycle=%ld, duration=%ld %s\n",
            link_fail_port, link_fail_start, link_fail_duration,
            link_fail_duration == 0 ? "(PERMANENT)" : "cycles");
  }
  
  fprintf(stdout, "ECMP mode: %s\n", 
          ecmp_mode == ECMP_HASH_FLOW ? "Hash-based (flow-aware)" : "Random (per-packet)");

  if ((LINKLATENCY % 7) != 0) {
    // if invalid link latency, error out.
    fprintf(stdout,
            "INVALID LINKLATENCY. Currently must be multiple of 7 cycles.\n");
    exit(1);
  }

  omp_set_num_threads(
      NUMPORTS); // we parallelize over ports, so max threads = # ports

#define PORTSETUPCONFIG
#include "switchconfig.h"
#undef PORTSETUPCONFIG

  // Configure link failure if specified
  if (link_fail_port >= 0 && link_fail_port < NUMPORTS) {
    ports[link_fail_port]->set_link_failure(link_fail_start, link_fail_duration);
  } else if (link_fail_port >= NUMPORTS) {
    fprintf(stderr, "ERROR: Invalid link_fail_port %d (only %d ports exist)\n", 
            link_fail_port, NUMPORTS);
    exit(1);
  }

#ifdef CAPTURE
  capture = fopen("capture.txt", "w");
  if (capture == NULL) {
    fprintf(stderr, "failed to open capture file");
    exit(1);
  }
#endif

  while (true) {

    // handle sends
#pragma omp parallel for
    for (int port = 0; port < NUMPORTS; port++) {
      ports[port]->send();
    }

    // handle receives. these are blocking per port
#pragma omp parallel for
    for (int port = 0; port < NUMPORTS; port++) {
      ports[port]->recv();
    }

    // update link states based on current cycle (for link failure simulation)
#pragma omp parallel for
    for (int port = 0; port < NUMPORTS; port++) {
      ports[port]->update_link_state(this_iter_cycles_start);
    }

#pragma omp parallel for
    for (int port = 0; port < NUMPORTS; port++) {
      ports[port]->tick_pre();
    }

    do_fast_switching();

    this_iter_cycles_start += LINKLATENCY; // keep track of time

    // some ports need to handle extra stuff after each iteration
    // e.g. shmem ports swapping shared buffers
#pragma omp parallel for
    for (int port = 0; port < NUMPORTS; port++) {
      ports[port]->tick();
    }
  }
}
