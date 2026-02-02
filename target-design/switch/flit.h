#ifndef __FLIT_H
#define __FLIT_H

#include <stdlib.h>
#include <stdint.h>

#define BROADCAST_ADJUSTED (0xffff)

// ECMP routing modes
#define ECMP_RANDOM 0     // Random selection (original behavior)
#define ECMP_HASH_FLOW 1  // Hash on 5-tuple for flow-level ECMP

extern int ecmp_mode;

/* ----------------------------------------------------
 * buffer flit operations
 *
 * ----------------------------------------------------
 */
// get a flit from recv_buf, given the token id
uint64_t get_flit(uint8_t *recv_buf, int tokenid) {
  int base = tokenid / TOKENS_PER_BIGTOKEN;
  int offset = tokenid % TOKENS_PER_BIGTOKEN;
  return *(((uint64_t *)recv_buf) + base * 8 + (offset + 1));
}

// write a flit to send_buf
void write_flit(uint8_t *send_buf, int tokenid, uint64_t flit) {
  int base = tokenid / TOKENS_PER_BIGTOKEN;
  int offset = tokenid % TOKENS_PER_BIGTOKEN;
  *(((uint64_t *)send_buf) + base * 8 + (offset + 1)) = flit;
}

// for a particular tokenid, determine if the flit is valid
int is_valid_flit(uint8_t *recv_buf, int tokenid) {
  int base = tokenid / TOKENS_PER_BIGTOKEN;
  int offset = tokenid % TOKENS_PER_BIGTOKEN;

  uint64_t lrv = ((uint64_t *)recv_buf)[base * 8];
  int bitoffset = 43 + (offset * 3);
  return (lrv >> bitoffset) & 0x1;
}

int is_last_flit(uint8_t *recv_buf, int tokenid) {
  int base = tokenid / TOKENS_PER_BIGTOKEN;
  int offset = tokenid % TOKENS_PER_BIGTOKEN;

  uint64_t lrv = ((uint64_t *)recv_buf)[base * 8];
  int bitoffset = 45 + (offset * 3);
  return (lrv >> bitoffset) & 0x1;
}

void write_valid_flit(uint8_t *send_buf, int tokenid) {
  int base = tokenid / TOKENS_PER_BIGTOKEN;
  int offset = tokenid % TOKENS_PER_BIGTOKEN;

  uint64_t *lrv = ((uint64_t *)send_buf) + base * 8;
  int bitoffset = 43 + (offset * 3);
  *lrv |= (1L << bitoffset);
}

void write_last_flit(uint8_t *send_buf, int tokenid, int is_last) {
  int base = tokenid / TOKENS_PER_BIGTOKEN;
  int offset = tokenid % TOKENS_PER_BIGTOKEN;

  uint64_t *lrv = ((uint64_t *)send_buf) + base * 8;
  int bitoffset = 45 + (offset * 3);
  *lrv |= (((uint64_t)is_last) << bitoffset);
}

/* Extract 5-tuple hash for ECMP flow-based routing
 * Attempts to extract: src_ip, dst_ip, src_port, dst_port, protocol
 * Returns a hash value for selecting among equal-cost paths
 */
uint32_t compute_flow_hash(uint64_t *packet_flits, int num_flits) {
  // Simple hash combining available header fields
  // Flit 0: Ethernet header (MACs)
  // Flit 1-2: IP header (if present)
  // Flit 3+: TCP/UDP header (if present)
  
  uint32_t hash = 0;
  
  // Include source MAC (bits 0-47 of flit 0) for basic diversity
  hash ^= (uint32_t)(packet_flits[0] & 0xFFFFFFFF);
  hash ^= (uint32_t)((packet_flits[0] >> 32) & 0xFFFF);
  
  // If we have enough flits, try to incorporate IP addresses and ports
  if (num_flits >= 3) {
    // IP addresses are typically in flits 1-2
    hash ^= (uint32_t)(packet_flits[1]);
    hash ^= (uint32_t)(packet_flits[2]);
  }
  
  // Simple mixing function (Jenkins one-at-a-time hash variant)
  hash += (hash << 10);
  hash ^= (hash >> 6);
  hash += (hash << 3);
  hash ^= (hash >> 11);
  hash += (hash << 15);
  
  return hash;
}

/* get dest mac from flit, then get port from mac */
uint16_t get_port_from_flit(uint64_t flit, int current_port) {
  uint16_t is_multicast = (flit >> 16) & 0x1;
  uint16_t flit_low = (flit >> 48) & 0xFFFF; // indicates dest
  uint16_t sendport = (__builtin_bswap16(flit_low));

  if (is_multicast)
    return BROADCAST_ADJUSTED;

  sendport = sendport & 0xFFFF;
  // printf("mac: %04x\n", sendport);

  // At this point, we know the MAC address is not a broadcast address,
  // so we can just look up the port in the mac2port table
  sendport = mac2port[sendport];

  // For uplink routing, support ECMP modes
  if (sendport == NUMDOWNLINKS) {
    // This destination requires uplink selection
    if (NUMUPLINKS == 1) {
      // Only one uplink, no choice
      sendport = NUMDOWNLINKS;
    } else {
      // Multiple uplinks available - use configured ECMP mode
      sendport = NUMDOWNLINKS;  // Will be resolved with full packet in do_fast_switching
    }
  }
  
  return sendport;
}

/* Select uplink port using ECMP policy 
 * Called when routing requires uplink selection
 */
uint16_t select_uplink_port_ecmp(uint64_t *packet_flits, int num_flits) {
  if (NUMUPLINKS == 1) {
    return NUMDOWNLINKS;  // Only one uplink
  }
  
  int uplink_index;
  
  if (ecmp_mode == ECMP_HASH_FLOW) {
    // Flow-based ECMP: Hash on packet headers for per-flow consistency
    uint32_t flow_hash = compute_flow_hash(packet_flits, num_flits);
    uplink_index = flow_hash % NUMUPLINKS;
  } else {
    // Random ECMP: Original behavior (per-packet randomization)
    uplink_index = rand() % NUMUPLINKS;
  }
  
  return NUMDOWNLINKS + uplink_index;
}
#endif // __FLIT_H
