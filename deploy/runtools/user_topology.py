""" Define your additional topologies here. The FireSimTopology class inherits
from UserToplogies and thus can instantiate your topology. """

from __future__ import annotations

from runtools.firesim_topology_elements import (
    FireSimPipeNode,
    FireSimSwitchNode,
    FireSimServerNode,
    FireSimSuperNodeServerNode,
    FireSimDummyServerNode,
    FireSimNode,
)
from runtools.simulation_data_classes import (
    PartitionConfig,
    PartitionMode,
    PartitionNode,
    FireAxeEdge,
    FireAxeNodeBridgePair,
)

from typing import (
    Optional,
    Union,
    Callable,
    Sequence,
    TYPE_CHECKING,
    cast,
    List,
    Any,
    Dict,
)

if TYPE_CHECKING:
    from runtools.firesim_topology_with_passes import FireSimTopologyWithPasses


class UserTopologies:
    """A class that just separates out user-defined/configurable topologies
    from the rest of the boilerplate in FireSimTopology()"""

    no_net_num_nodes: int
    custom_mapper: Optional[Union[Callable, str]]
    roots: Sequence[FireSimNode]

    def __init__(self, no_net_num_nodes: int) -> None:
        self.no_net_num_nodes = no_net_num_nodes
        self.custom_mapper = None
        self.roots = []

    def clos_m_n_r(self, m: int, n: int, r: int) -> None:
        """DO NOT USE THIS DIRECTLY, USE ONE OF THE INSTANTIATIONS BELOW."""
        """ Clos topol where:
        m = number of root switches
        n = number of links to nodes on leaf switches
        r = number of leaf switches

        and each leaf switch has a link to each root switch.

        With the default mapping specified below, you will need:
        m switch nodes (on F1: m4.16xlarges).
        n fpga nodes (on F1: f1.16xlarges).

        TODO: improve this later to pack leaf switches with <= 4 downlinks onto
        one 16x.large.
        """

        rootswitches = [FireSimSwitchNode() for x in range(m)]
        self.roots = rootswitches
        leafswitches = [FireSimSwitchNode() for x in range(r)]
        servers = [[FireSimServerNode() for x in range(n)] for y in range(r)]
        for rswitch in rootswitches:
            rswitch.add_downlinks(leafswitches)

        for leafswitch, servergroup in zip(leafswitches, servers):
            leafswitch.add_downlinks(servergroup)

        def custom_mapper(fsim_topol_with_passes: FireSimTopologyWithPasses) -> None:
            for i, rswitch in enumerate(rootswitches):
                switch_inst_handle = (
                    fsim_topol_with_passes.run_farm.get_switch_only_host_handle()
                )
                switch_inst = fsim_topol_with_passes.run_farm.allocate_sim_host(
                    switch_inst_handle
                )
                switch_inst.add_switch(rswitch)

            for j, lswitch in enumerate(leafswitches):
                numsims = len(servers[j])
                inst_handle = (
                    fsim_topol_with_passes.run_farm.get_smallest_sim_host_handle(
                        num_sims=numsims
                    )
                )
                sim_inst = fsim_topol_with_passes.run_farm.allocate_sim_host(
                    inst_handle
                )
                sim_inst.add_switch(lswitch)
                for sim in servers[j]:
                    sim_inst.add_simulation(sim)

        self.custom_mapper = custom_mapper

    def clos_2_8_2(self) -> None:
        """clos topol with:
        2 roots
        8 nodes/leaf
        2 leaves."""
        self.clos_m_n_r(2, 8, 2)

    def clos_8_8_16(self) -> None:
        """clos topol with:
        8 roots
        8 nodes/leaf
        16 leaves. = 128 nodes."""
        self.clos_m_n_r(8, 8, 16)

    def fat_tree_4ary(self) -> None:
        # 4-ary fat tree as described in
        # http://ccr.sigcomm.org/online/files/p63-alfares.pdf
        coreswitches = [FireSimSwitchNode() for x in range(4)]
        self.roots = coreswitches
        aggrswitches = [FireSimSwitchNode() for x in range(8)]
        edgeswitches = [FireSimSwitchNode() for x in range(8)]
        servers = [FireSimServerNode() for x in range(16)]
        for switchno in range(len(coreswitches)):
            core = coreswitches[switchno]
            base = 0 if switchno < 2 else 1
            dls = list(map(lambda x: aggrswitches[x], range(base, 8, 2)))
            core.add_downlinks(dls)
        for switchbaseno in range(0, len(aggrswitches), 2):
            switchno = switchbaseno + 0
            aggr = aggrswitches[switchno]
            aggr.add_downlinks([edgeswitches[switchno], edgeswitches[switchno + 1]])
            switchno = switchbaseno + 1
            aggr = aggrswitches[switchno]
            aggr.add_downlinks([edgeswitches[switchno - 1], edgeswitches[switchno]])
        for edgeno in range(len(edgeswitches)):
            edgeswitches[edgeno].add_downlinks(
                [servers[edgeno * 2], servers[edgeno * 2 + 1]]
            )

        def custom_mapper(fsim_topol_with_passes: FireSimTopologyWithPasses) -> None:
            """In a custom mapper, you have access to the firesim topology with passes,
            where you can access the run_farm nodes:

            Requires 2 fpga nodes w/ 8+ fpgas and 1 switch node

            To map, call add_switch or add_simulation on run farm instance
            objs in the aforementioned arrays.

            Because of the scope of this fn, you also have access to whatever
            stuff you created in the topology itself, which we expect will be
            useful for performing the mapping."""

            # map the fat tree onto one switch host instance (for core switches)
            # and two 8-sim-slot (e.g. 8-fpga) instances
            # (e.g., two pods of aggr/edge/4sims per f1.16xlarge)

            switch_inst_handle = (
                fsim_topol_with_passes.run_farm.get_switch_only_host_handle()
            )
            switch_inst = fsim_topol_with_passes.run_farm.allocate_sim_host(
                switch_inst_handle
            )
            for core in coreswitches:
                switch_inst.add_switch(core)

            eight_sim_host_handle = (
                fsim_topol_with_passes.run_farm.get_smallest_sim_host_handle(num_sims=8)
            )
            sim_hosts = [
                fsim_topol_with_passes.run_farm.allocate_sim_host(eight_sim_host_handle)
                for _ in range(2)
            ]

            for aggrsw in aggrswitches[:4]:
                sim_hosts[0].add_switch(aggrsw)
            for aggrsw in aggrswitches[4:]:
                sim_hosts[1].add_switch(aggrsw)

            for edgesw in edgeswitches[:4]:
                sim_hosts[0].add_switch(edgesw)
            for edgesw in edgeswitches[4:]:
                sim_hosts[1].add_switch(edgesw)

            for sim in servers[:8]:
                sim_hosts[0].add_simulation(sim)
            for sim in servers[8:]:
                sim_hosts[1].add_simulation(sim)

        self.custom_mapper = custom_mapper

    def example_multilink(self) -> None:
        self.roots = [FireSimSwitchNode()]
        midswitch = FireSimSwitchNode()
        lowerlayer = [midswitch for x in range(16)]
        self.roots[0].add_downlinks(lowerlayer)
        servers = [FireSimServerNode()]
        midswitch.add_downlinks(servers)

    def example_multilink_32(self) -> None:
        self.roots = [FireSimSwitchNode()]
        midswitch = FireSimSwitchNode()
        lowerlayer = [midswitch for x in range(32)]
        self.roots[0].add_downlinks(lowerlayer)
        servers = [FireSimServerNode()]
        midswitch.add_downlinks(servers)

    def example_multilink_64(self) -> None:
        self.roots = [FireSimSwitchNode()]
        midswitch = FireSimSwitchNode()
        lowerlayer = [midswitch for x in range(64)]
        self.roots[0].add_downlinks(lowerlayer)
        servers = [FireSimServerNode()]
        midswitch.add_downlinks(servers)

    def example_cross_links(self) -> None:
        self.roots = [FireSimSwitchNode() for x in range(2)]
        midswitches = [FireSimSwitchNode() for x in range(2)]
        self.roots[0].add_downlinks(midswitches)
        self.roots[1].add_downlinks(midswitches)
        servers = [FireSimServerNode() for x in range(2)]
        midswitches[0].add_downlinks([servers[0]])
        midswitches[1].add_downlinks([servers[1]])

    def small_hierarchy_8sims(self) -> None:
        self.custom_mapper = "mapping_use_one_8_slot_node"
        self.roots = [FireSimSwitchNode()]
        midlevel = [FireSimSwitchNode() for x in range(4)]
        servers = [[FireSimServerNode() for x in range(2)] for x in range(4)]
        self.roots[0].add_downlinks(midlevel)
        for swno in range(len(midlevel)):
            midlevel[swno].add_downlinks(servers[swno])

    def small_hierarchy_2sims(self) -> None:
        self.custom_mapper = "mapping_use_one_8_slot_node"
        self.roots = [FireSimSwitchNode()]
        midlevel = [FireSimSwitchNode() for x in range(1)]
        servers = [[FireSimServerNode() for x in range(2)] for x in range(1)]
        self.roots[0].add_downlinks(midlevel)
        for swno in range(len(midlevel)):
            midlevel[swno].add_downlinks(servers[swno])

    def example_1config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        servers = [FireSimServerNode() for y in range(1)]
        self.roots[0].add_downlinks(servers)

    def example_2config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        servers = [FireSimServerNode() for y in range(2)]
        self.roots[0].add_downlinks(servers)

    def example_4config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        servers = [FireSimServerNode() for y in range(4)]
        self.roots[0].add_downlinks(servers)

    def example_8config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        servers = [FireSimServerNode() for y in range(8)]
        self.roots[0].add_downlinks(servers)

    def example_16config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level2switches = [FireSimSwitchNode() for x in range(2)]
        servers = [[FireSimServerNode() for y in range(8)] for x in range(2)]

        for root in self.roots:
            root.add_downlinks(level2switches)

        for l2switchNo in range(len(level2switches)):
            level2switches[l2switchNo].add_downlinks(servers[l2switchNo])

    def example_32config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level2switches = [FireSimSwitchNode() for x in range(4)]
        servers = [[FireSimServerNode() for y in range(8)] for x in range(4)]

        for root in self.roots:
            root.add_downlinks(level2switches)

        for l2switchNo in range(len(level2switches)):
            level2switches[l2switchNo].add_downlinks(servers[l2switchNo])

    def example_64config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level2switches = [FireSimSwitchNode() for x in range(8)]
        servers = [[FireSimServerNode() for y in range(8)] for x in range(8)]

        for root in self.roots:
            root.add_downlinks(level2switches)

        for l2switchNo in range(len(level2switches)):
            level2switches[l2switchNo].add_downlinks(servers[l2switchNo])

    def example_128config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level1switches = [FireSimSwitchNode() for x in range(2)]
        level2switches = [[FireSimSwitchNode() for x in range(8)] for x in range(2)]
        servers = [
            [[FireSimServerNode() for y in range(8)] for x in range(8)]
            for x in range(2)
        ]

        self.roots[0].add_downlinks(level1switches)

        for switchno in range(len(level1switches)):
            level1switches[switchno].add_downlinks(level2switches[switchno])

        for switchgroupno in range(len(level2switches)):
            for switchno in range(len(level2switches[switchgroupno])):
                level2switches[switchgroupno][switchno].add_downlinks(
                    servers[switchgroupno][switchno]
                )

    def example_256config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level1switches = [FireSimSwitchNode() for x in range(4)]
        level2switches = [[FireSimSwitchNode() for x in range(8)] for x in range(4)]
        servers = [
            [[FireSimServerNode() for y in range(8)] for x in range(8)]
            for x in range(4)
        ]

        self.roots[0].add_downlinks(level1switches)

        for switchno in range(len(level1switches)):
            level1switches[switchno].add_downlinks(level2switches[switchno])

        for switchgroupno in range(len(level2switches)):
            for switchno in range(len(level2switches[switchgroupno])):
                level2switches[switchgroupno][switchno].add_downlinks(
                    servers[switchgroupno][switchno]
                )

    @staticmethod
    def supernode_flatten(arr: List[Any]) -> List[Any]:
        res: List[Any] = []
        for x in arr:
            res = res + x
        return res

    def supernode_example_6config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        self.roots[0].add_downlinks([FireSimSuperNodeServerNode()])
        self.roots[0].add_downlinks([FireSimDummyServerNode() for x in range(5)])

    def supernode_example_4config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        self.roots[0].add_downlinks([FireSimSuperNodeServerNode()])
        self.roots[0].add_downlinks([FireSimDummyServerNode() for x in range(3)])

    def supernode_example_8config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        servers = UserTopologies.supernode_flatten(
            [
                [
                    FireSimSuperNodeServerNode(),
                    FireSimDummyServerNode(),
                    FireSimDummyServerNode(),
                    FireSimDummyServerNode(),
                ]
                for y in range(2)
            ]
        )
        self.roots[0].add_downlinks(servers)

    def supernode_example_16config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        servers = UserTopologies.supernode_flatten(
            [
                [
                    FireSimSuperNodeServerNode(),
                    FireSimDummyServerNode(),
                    FireSimDummyServerNode(),
                    FireSimDummyServerNode(),
                ]
                for y in range(4)
            ]
        )
        self.roots[0].add_downlinks(servers)

    def supernode_example_32config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        servers = UserTopologies.supernode_flatten(
            [
                [
                    FireSimSuperNodeServerNode(),
                    FireSimDummyServerNode(),
                    FireSimDummyServerNode(),
                    FireSimDummyServerNode(),
                ]
                for y in range(8)
            ]
        )
        self.roots[0].add_downlinks(servers)

    def supernode_example_64config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level2switches = [FireSimSwitchNode() for x in range(2)]
        servers = [
            UserTopologies.supernode_flatten(
                [
                    [
                        FireSimSuperNodeServerNode(),
                        FireSimDummyServerNode(),
                        FireSimDummyServerNode(),
                        FireSimDummyServerNode(),
                    ]
                    for y in range(8)
                ]
            )
            for x in range(2)
        ]
        for root in self.roots:
            root.add_downlinks(level2switches)
        for l2switchNo in range(len(level2switches)):
            level2switches[l2switchNo].add_downlinks(servers[l2switchNo])

    def supernode_example_128config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level2switches = [FireSimSwitchNode() for x in range(4)]
        servers = [
            UserTopologies.supernode_flatten(
                [
                    [
                        FireSimSuperNodeServerNode(),
                        FireSimDummyServerNode(),
                        FireSimDummyServerNode(),
                        FireSimDummyServerNode(),
                    ]
                    for y in range(8)
                ]
            )
            for x in range(4)
        ]
        for root in self.roots:
            root.add_downlinks(level2switches)
        for l2switchNo in range(len(level2switches)):
            level2switches[l2switchNo].add_downlinks(servers[l2switchNo])

    def supernode_example_256config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level2switches = [FireSimSwitchNode() for x in range(8)]
        servers = [
            UserTopologies.supernode_flatten(
                [
                    [
                        FireSimSuperNodeServerNode(),
                        FireSimDummyServerNode(),
                        FireSimDummyServerNode(),
                        FireSimDummyServerNode(),
                    ]
                    for y in range(8)
                ]
            )
            for x in range(8)
        ]
        for root in self.roots:
            root.add_downlinks(level2switches)
        for l2switchNo in range(len(level2switches)):
            level2switches[l2switchNo].add_downlinks(servers[l2switchNo])

    def supernode_example_512config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level1switches = [FireSimSwitchNode() for x in range(2)]
        level2switches = [[FireSimSwitchNode() for x in range(8)] for x in range(2)]
        servers = [
            [
                UserTopologies.supernode_flatten(
                    [
                        [
                            FireSimSuperNodeServerNode(),
                            FireSimDummyServerNode(),
                            FireSimDummyServerNode(),
                            FireSimDummyServerNode(),
                        ]
                        for y in range(8)
                    ]
                )
                for x in range(8)
            ]
            for x in range(2)
        ]
        self.roots[0].add_downlinks(level1switches)
        for switchno in range(len(level1switches)):
            level1switches[switchno].add_downlinks(level2switches[switchno])
        for switchgroupno in range(len(level2switches)):
            for switchno in range(len(level2switches[switchgroupno])):
                level2switches[switchgroupno][switchno].add_downlinks(
                    servers[switchgroupno][switchno]
                )

    def supernode_example_1024config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level1switches = [FireSimSwitchNode() for x in range(4)]
        level2switches = [[FireSimSwitchNode() for x in range(8)] for x in range(4)]
        servers = [
            [
                UserTopologies.supernode_flatten(
                    [
                        [
                            FireSimSuperNodeServerNode(),
                            FireSimDummyServerNode(),
                            FireSimDummyServerNode(),
                            FireSimDummyServerNode(),
                        ]
                        for y in range(8)
                    ]
                )
                for x in range(8)
            ]
            for x in range(4)
        ]
        self.roots[0].add_downlinks(level1switches)
        for switchno in range(len(level1switches)):
            level1switches[switchno].add_downlinks(level2switches[switchno])
        for switchgroupno in range(len(level2switches)):
            for switchno in range(len(level2switches[switchgroupno])):
                level2switches[switchgroupno][switchno].add_downlinks(
                    servers[switchgroupno][switchno]
                )

    def supernode_example_deep64config(self) -> None:
        self.roots = [FireSimSwitchNode()]
        level1switches = [FireSimSwitchNode() for x in range(2)]
        level2switches = [[FireSimSwitchNode() for x in range(1)] for x in range(2)]
        servers = [
            [
                UserTopologies.supernode_flatten(
                    [
                        [
                            FireSimSuperNodeServerNode(),
                            FireSimDummyServerNode(),
                            FireSimDummyServerNode(),
                            FireSimDummyServerNode(),
                        ]
                        for y in range(8)
                    ]
                )
                for x in range(1)
            ]
            for x in range(2)
        ]
        self.roots[0].add_downlinks(level1switches)
        for switchno in range(len(level1switches)):
            level1switches[switchno].add_downlinks(level2switches[switchno])
        for switchgroupno in range(len(level2switches)):
            for switchno in range(len(level2switches[switchgroupno])):
                level2switches[switchgroupno][switchno].add_downlinks(
                    servers[switchgroupno][switchno]
                )

    def dual_example_8config(self) -> None:
        """two separate 8-node clusters for experiments, e.g. memcached mutilate."""
        self.roots = [FireSimSwitchNode()] * 2
        servers = [FireSimServerNode() for y in range(8)]
        servers2 = [FireSimServerNode() for y in range(8)]
        self.roots[0].add_downlinks(servers)
        self.roots[1].add_downlinks(servers2)

    def triple_example_8config(self) -> None:
        """three separate 8-node clusters for experiments, e.g. memcached mutilate."""
        self.roots = [FireSimSwitchNode()] * 3
        servers = [FireSimServerNode() for y in range(8)]
        servers2 = [FireSimServerNode() for y in range(8)]
        servers3 = [FireSimServerNode() for y in range(8)]
        self.roots[0].add_downlinks(servers)
        self.roots[1].add_downlinks(servers2)
        self.roots[2].add_downlinks(servers3)

    def no_net_config(self) -> None:
        self.roots = [FireSimServerNode() for x in range(self.no_net_num_nodes)]

    # Spins up all of the precompiled, unnetworked targets
    def all_no_net_targets_config(self) -> None:
        hwdb_entries = [
            "firesim_boom_singlecore_no_nic_l2_llc4mb_ddr3",
            "firesim_rocket_quadcore_no_nic_l2_llc4mb_ddr3",
        ]
        assert len(hwdb_entries) == self.no_net_num_nodes
        self.roots = [
            FireSimServerNode(hwdb_entries[x]) for x in range(self.no_net_num_nodes)
        ]

    ########################################################################
    # Torus and Dragonfly Topologies

    def torus_topology(
        self,
        dim_x: int,
        dim_y: int,
        dim_z: Optional[int] = None,
        servers_per_switch: int = 1,
    ) -> None:
        """Create a 2D or 3D torus network topology.

        In a torus topology, switches are arranged in a grid pattern with
        wrap-around edges. Each switch connects to its immediate neighbors
        in each dimension.

        Args:
            dim_x: Size of the torus in the X dimension
            dim_y: Size of the torus in the Y dimension
            dim_z: Optional size in Z dimension for 3D torus. If None, creates 2D torus.
            servers_per_switch: Number of server nodes attached to each switch

        Example:
            torus_topology(dim_x=4, dim_y=4)  # 4x4 2D torus with 16 switches
            torus_topology(dim_x=4, dim_y=4, dim_z=4)  # 4x4x4 3D torus with 64 switches
        """
        if dim_z is None:
            # 2D Torus
            total_switches = dim_x * dim_y
            switches = [[FireSimSwitchNode() for _ in range(dim_y)] for _ in range(dim_x)]
            servers = [
                [FireSimServerNode() for _ in range(servers_per_switch)]
                for _ in range(total_switches)
            ]

            # Flatten switches for easier indexing
            switch_list = []
            for i in range(dim_x):
                for j in range(dim_y):
                    switch_list.append(switches[i][j])

            # Connect switches in X dimension (with wrap-around)
            # Each link is bidirectional - uplinks are automatically created
            for i in range(dim_x):
                for j in range(dim_y):
                    # Connect to neighbor in +X direction (wrap-around)
                    x_neighbor = switches[(i + 1) % dim_x][j]
                    switches[i][j].add_downlink(x_neighbor)

            # Connect switches in Y dimension (with wrap-around)
            for i in range(dim_x):
                for j in range(dim_y):
                    # Connect to neighbor in +Y direction (wrap-around)
                    y_neighbor = switches[i][(j + 1) % dim_y]
                    switches[i][j].add_downlink(y_neighbor)

            # Attach servers to switches
            server_idx = 0
            for i in range(dim_x):
                for j in range(dim_y):
                    switches[i][j].add_downlinks(servers[server_idx])
                    server_idx += 1

            # Set roots to first row of switches (arbitrary choice for tree-like structure)
            self.roots = switches[0]

        else:
            # 3D Torus
            total_switches = dim_x * dim_y * dim_z
            switches = [
                [
                    [FireSimSwitchNode() for _ in range(dim_z)]
                    for _ in range(dim_y)
                ]
                for _ in range(dim_x)
            ]
            servers = [
                [FireSimServerNode() for _ in range(servers_per_switch)]
                for _ in range(total_switches)
            ]

            # Flatten switches for easier indexing
            switch_list = []
            for i in range(dim_x):
                for j in range(dim_y):
                    for k in range(dim_z):
                        switch_list.append(switches[i][j][k])

            # Connect switches in X dimension (with wrap-around)
            # Each link is bidirectional - uplinks are automatically created
            for i in range(dim_x):
                for j in range(dim_y):
                    for k in range(dim_z):
                        # Connect to neighbor in +X direction (wrap-around)
                        x_neighbor = switches[(i + 1) % dim_x][j][k]
                        switches[i][j][k].add_downlink(x_neighbor)

            # Connect switches in Y dimension (with wrap-around)
            for i in range(dim_x):
                for j in range(dim_y):
                    for k in range(dim_z):
                        # Connect to neighbor in +Y direction (wrap-around)
                        y_neighbor = switches[i][(j + 1) % dim_y][k]
                        switches[i][j][k].add_downlink(y_neighbor)

            # Connect switches in Z dimension (with wrap-around)
            for i in range(dim_x):
                for j in range(dim_y):
                    for k in range(dim_z):
                        # Connect to neighbor in +Z direction (wrap-around)
                        z_neighbor = switches[i][j][(k + 1) % dim_z]
                        switches[i][j][k].add_downlink(z_neighbor)

            # Attach servers to switches
            server_idx = 0
            for i in range(dim_x):
                for j in range(dim_y):
                    for k in range(dim_z):
                        switches[i][j][k].add_downlinks(servers[server_idx])
                        server_idx += 1

            # Set roots to first row of first plane (arbitrary choice for tree-like structure)
            self.roots = switches[0][0]

        # Default mapping: round-robin allocation
        def custom_mapper(fsim_topol_with_passes: FireSimTopologyWithPasses) -> None:
            """Default mapper for torus topology using round-robin allocation."""
            # Get all switches and servers
            all_switches = fsim_topol_with_passes.firesimtopol.get_dfs_order_switches()
            all_servers = fsim_topol_with_passes.firesimtopol.get_dfs_order_servers()

            # Allocate switches to switch-only hosts
            switch_inst_handle = (
                fsim_topol_with_passes.run_farm.get_switch_only_host_handle()
            )
            switch_inst = fsim_topol_with_passes.run_farm.allocate_sim_host(
                switch_inst_handle
            )
            for switch in all_switches:
                switch_inst.add_switch(switch)

            # Allocate servers to sim hosts using round-robin to pack efficiently
            # Allocate servers in round-robin fashion across available hosts
            current_host = None
            servers_on_current_host = 0
            max_servers_per_host = 0
            
            for idx, server in enumerate(all_servers):
                # Allocate a new host when needed
                if current_host is None or servers_on_current_host >= max_servers_per_host:
                    try:
                        inst_handle_for_one = (
                            fsim_topol_with_passes.run_farm.get_smallest_sim_host_handle(
                                num_sims=1
                            )
                        )
                        current_host = fsim_topol_with_passes.run_farm.allocate_sim_host(
                            inst_handle_for_one
                        )
                        # Get the max capacity of this host
                        if fsim_topol_with_passes.run_farm.metasimulation_enabled:
                            max_servers_per_host = fsim_topol_with_passes.run_farm.SIM_HOST_HANDLE_TO_MAX_METASIM_SLOTS.get(
                                inst_handle_for_one, 1
                            )
                        else:
                            max_servers_per_host = fsim_topol_with_passes.run_farm.SIM_HOST_HANDLE_TO_MAX_FPGA_SLOTS.get(
                                inst_handle_for_one, 1
                            )
                        servers_on_current_host = 0
                    except:
                        # If we run out of hosts, pack remaining on the last host
                        pass
                
                if current_host is not None:
                    current_host.add_simulation(server)
                    servers_on_current_host += 1

        self.custom_mapper = custom_mapper

    def dragonfly_topology(
        self,
        groups: int,
        switches_per_group: int,
        local_degree: int,
        global_degree: int,
        servers_per_switch: int = 1,
    ) -> None:
        """Create a dragonfly network topology.

        A dragonfly topology consists of groups of switches. Each switch has:
        - Local links: connections to other switches within the same group
        - Global links: connections to switches in other groups

        The topology aims for minimal diameter (often 3 hops).

        Args:
            groups: Number of groups in the topology
            switches_per_group: Number of switches in each group
            local_degree: Number of local links per switch (to other switches in same group)
            global_degree: Number of global links per switch (to switches in other groups)
            servers_per_switch: Number of server nodes attached to each switch

        Example:
            dragonfly_topology(
                groups=4,
                switches_per_group=8,
                local_degree=4,
                global_degree=2
            )
        """
        total_switches = groups * switches_per_group

        # Validate parameters
        if local_degree >= switches_per_group:
            raise ValueError(
                f"local_degree ({local_degree}) must be less than "
                f"switches_per_group ({switches_per_group})"
            )
        if global_degree >= groups:
            raise ValueError(
                f"global_degree ({global_degree}) must be less than groups ({groups})"
            )

        # Create switches organized by groups
        switch_groups: List[List[FireSimSwitchNode]] = []
        for g in range(groups):
            group_switches = [FireSimSwitchNode() for _ in range(switches_per_group)]
            switch_groups.append(group_switches)

        # Create servers for each switch
        servers = [
            [FireSimServerNode() for _ in range(servers_per_switch)]
            for _ in range(total_switches)
        ]

        # Create local links within each group
        # Use a simple pattern: connect each switch to its next 'local_degree' neighbors
        for group_idx, group_switches in enumerate(switch_groups):
            for switch_idx, switch in enumerate(group_switches):
                # Connect to local_degree neighbors in a round-robin fashion
                for link_offset in range(1, local_degree + 1):
                    neighbor_idx = (switch_idx + link_offset) % switches_per_group
                    neighbor = group_switches[neighbor_idx]
                    switch.add_downlink(neighbor)

        # Create global links between groups
        # Each switch connects to switches in other groups
        # Use a deterministic pattern to ensure connectivity
        for group_idx, group_switches in enumerate(switch_groups):
            for switch_idx, switch in enumerate(group_switches):
                # Determine which groups this switch should connect to
                # Use a pattern that distributes global links evenly
                target_groups = []
                for g_offset in range(1, groups):
                    target_group = (group_idx + g_offset) % groups
                    target_groups.append(target_group)

                # Select global_degree groups to connect to
                # Use switch index to determine which groups to connect to
                selected_groups = []
                for i in range(global_degree):
                    if i < len(target_groups):
                        selected_groups.append(target_groups[i])

                # Connect to one switch in each selected group
                for target_group_idx in selected_groups:
                    target_group = switch_groups[target_group_idx]
                    # Use switch index to determine which switch in target group
                    target_switch_idx = (
                        switch_idx + group_idx
                    ) % switches_per_group
                    target_switch = target_group[target_switch_idx]
                    switch.add_downlink(target_switch)

        # Attach servers to switches
        server_idx = 0
        for group_switches in switch_groups:
            for switch in group_switches:
                switch.add_downlinks(servers[server_idx])
                server_idx += 1

        # Set roots to first switch of first group (arbitrary choice)
        self.roots = [switch_groups[0][0]]

        # Default mapping: allocate switches and servers
        def custom_mapper(fsim_topol_with_passes: FireSimTopologyWithPasses) -> None:
            """Default mapper for dragonfly topology."""
            # Get all switches and servers
            all_switches = fsim_topol_with_passes.firesimtopol.get_dfs_order_switches()
            all_servers = fsim_topol_with_passes.firesimtopol.get_dfs_order_servers()

            # Allocate switches to switch-only hosts
            # For simplicity, put all switches on one host
            switch_inst_handle = (
                fsim_topol_with_passes.run_farm.get_switch_only_host_handle()
            )
            switch_inst = fsim_topol_with_passes.run_farm.allocate_sim_host(
                switch_inst_handle
            )
            for switch in all_switches:
                switch_inst.add_switch(switch)

            # Allocate servers to sim hosts using round-robin to pack efficiently
            # Allocate servers in round-robin fashion across available hosts
            current_host = None
            servers_on_current_host = 0
            max_servers_per_host = 0
            
            for idx, server in enumerate(all_servers):
                # Allocate a new host when needed
                if current_host is None or servers_on_current_host >= max_servers_per_host:
                    try:
                        inst_handle_for_one = (
                            fsim_topol_with_passes.run_farm.get_smallest_sim_host_handle(
                                num_sims=1
                            )
                        )
                        current_host = fsim_topol_with_passes.run_farm.allocate_sim_host(
                            inst_handle_for_one
                        )
                        # Get the max capacity of this host
                        if fsim_topol_with_passes.run_farm.metasimulation_enabled:
                            max_servers_per_host = fsim_topol_with_passes.run_farm.SIM_HOST_HANDLE_TO_MAX_METASIM_SLOTS.get(
                                inst_handle_for_one, 1
                            )
                        else:
                            max_servers_per_host = fsim_topol_with_passes.run_farm.SIM_HOST_HANDLE_TO_MAX_FPGA_SLOTS.get(
                                inst_handle_for_one, 1
                            )
                        servers_on_current_host = 0
                    except:
                        # If we run out of hosts, pack remaining on the last host
                        pass
                
                if current_host is not None:
                    current_host.add_simulation(server)
                    servers_on_current_host += 1

        self.custom_mapper = custom_mapper

    # Convenience methods for common torus configurations
    def torus_4x4(self) -> None:
        """4x4 2D torus topology with 16 switches."""
        self.torus_topology(dim_x=4, dim_y=4)

    def torus_8x8(self) -> None:
        """8x8 2D torus topology with 64 switches."""
        self.torus_topology(dim_x=8, dim_y=8)

    def torus_4x4x4(self) -> None:
        """4x4x4 3D torus topology with 64 switches."""
        self.torus_topology(dim_x=4, dim_y=4, dim_z=4)

    # Convenience methods for common dragonfly configurations
    def dragonfly_4_8_4_2(self) -> None:
        """Dragonfly topology: 4 groups, 8 switches/group, local_degree=4, global_degree=2."""
        self.dragonfly_topology(
            groups=4, switches_per_group=8, local_degree=4, global_degree=2
        )
        
    def dragonfly_2_8_4_2(self) -> None:
        """Dragonfly topology: 4 groups, 8 switches/group, local_degree=4, global_degree=2."""
        self.dragonfly_topology(
            groups=4, switches_per_group=8, local_degree=4, global_degree=2
        )

    def dragonfly_8_16_8_4(self) -> None:
        """Dragonfly topology: 8 groups, 16 switches/group, local_degree=8, global_degree=4."""
        self.dragonfly_topology(
            groups=8, switches_per_group=16, local_degree=8, global_degree=4
        )

    ########################################################################

    # DOC include start: user_topology.py fireaxe_topology_config
    def fireaxe_topology_config(
        self,
        hwdb_entries: Dict[int, str],
        edges: List[FireAxeEdge],
        slotid_to_pidx: List[int],
        mode: PartitionMode,
    ) -> None:
        pidx_to_slotid = dict()
        for sid, pid in enumerate(slotid_to_pidx):
            pidx_to_slotid[pid] = sid

        # Create partition nodes
        pidx_to_partition_node: Dict[int, PartitionNode] = dict()
        for pidx, hwdb in hwdb_entries.items():
            node = PartitionNode(hwdb, pidx)
            pidx_to_partition_node[pidx] = node

        # Add edges to nodes
        for edge in edges:
            u_node = pidx_to_partition_node[edge.u.pidx]
            v_node = pidx_to_partition_node[edge.v.pidx]
            u_node.add_edge(edge.u.bidx, edge.v.bidx, v_node)
            v_node.add_edge(edge.v.bidx, edge.u.bidx, u_node)

        # Create PartitionConfigs and FireSimServerNode
        servers: Dict[int, FireSimServerNode] = dict()
        for pidx, node in pidx_to_partition_node.items():
            partition_cfg = PartitionConfig(node, pidx_to_slotid, mode)
            servers[pidx_to_slotid[node.pidx]] = FireSimServerNode(
                partition_cfg.get_hwdb(), partition_config=partition_cfg
            )

        # Sort the servers by their sim slot id
        servers = dict(sorted(servers.items()))
        self.roots = list(servers.values())

    # DOC include end: user_topology.py fireaxe_topology_config

    # DOC include start: user_topology.py fireaxe_rocket_fastmode_config
    def fireaxe_rocket_fastmode_config(self) -> None:
        # DOC include start: fireaxe_fastmode_config hwdb_entries
        # hwdb_entries maps the partition index to the hwdb name
        hwdb_entries = {0: "f1_rocket_split_soc_fast", 1: "f1_rocket_split_tile_fast"}
        # DOC include end: fireaxe_fastmode_config hwdb_entries
        # DOC include start: fireaxe_fastmode_config slot_to_pidx
        # slotid_to_pidx maps the partition index to the FPGA slotid
        # For instance, `slotid_to_pidx = [2, 1, 0]` will map partition
        # index 2 to simulation slot 0, partition index 1 to simulation slot 1,
        # and partition index 0 to simulation slot 2.
        slotid_to_pidx = [0, 1]
        # DOC include end: fireaxe_fastmode_config slot_to_pidx
        # DOC include start: fireaxe_fastmode_config edges
        # The `FireAxeEdge` class is used to depict the connections between the partitions.
        edges = [FireAxeEdge(FireAxeNodeBridgePair(0, 0), FireAxeNodeBridgePair(1, 0))]
        # DOC include end: fireaxe_fastmode_config edges
        # DOC include start: fireaxe_fastmode_config mode
        # The PartitionMode enum contains the different partition modes that are available
        mode = PartitionMode.FAST_MODE
        # DOC include end: fireaxe_fastmode_config mode
        # DOC include start: fireaxe_fastmode_config summing it all up
        self.fireaxe_topology_config(hwdb_entries, edges, slotid_to_pidx, mode)
        # DOC include end: fireaxe_fastmode_config summing it all up

    # DOC include end: user_topology.py fireaxe_rocket_fastmode_config

    # DOC include start: user_topology.py fireaxe_rocket_exactmode_config
    def fireaxe_rocket_exactmode_config(self) -> None:
        hwdb_entries = {
            0: "f1_firesim_rocket_tile_exact",
            1: "f1_firesim_rocket_soc_exact",
        }
        slotid_to_pidx = [0, 1]
        edges = [
            # DOC include start: fireaxe_rocket_exactmode_config edge 0
            FireAxeEdge(FireAxeNodeBridgePair(0, 0), FireAxeNodeBridgePair(1, 0)),
            # DOC include end: fireaxe_rocket_exactmode_config edge 0
            # DOC include start: fireaxe_rocket_exactmode_config edge 1
            FireAxeEdge(FireAxeNodeBridgePair(0, 1), FireAxeNodeBridgePair(1, 1)),
            # DOC include end: fireaxe_rocket_exactmode_config edge 1
        ]
        # DOC include start: fireaxe_rocket_exactmode_config mode
        mode = PartitionMode.EXACT_MODE
        # DOC include end: fireaxe_rocket_exactmode_config mode
        self.fireaxe_topology_config(hwdb_entries, edges, slotid_to_pidx, mode)

    # DOC include end: user_topology.py fireaxe_rocket_exactmode_config

    # DOC include start: user_topology.py fireaxe_ring_noc_config
    def fireaxe_ring_noc_config(self) -> None:
        hwdb_entries = {
            0: "xilinx_u250_quad_rocket_ring_0",
            1: "xilinx_u250_quad_rocket_ring_1",
            2: "xilinx_u250_quad_rocket_ring_base",
        }
        slotid_to_pidx = [0, 1, 2]
        # DOC include start: fireaxe_ring_noc_config edges
        edges = [
            FireAxeEdge(FireAxeNodeBridgePair(0, 0), FireAxeNodeBridgePair(2, 1)),
            FireAxeEdge(FireAxeNodeBridgePair(2, 0), FireAxeNodeBridgePair(1, 1)),
            FireAxeEdge(FireAxeNodeBridgePair(1, 0), FireAxeNodeBridgePair(0, 1)),
        ]
        # DOC include end: fireaxe_ring_noc_config edges
        mode = PartitionMode.NOC_MODE
        self.fireaxe_topology_config(hwdb_entries, edges, slotid_to_pidx, mode)

    # DOC include end: user_topology.py fireaxe_ring_noc_config


#    ######Used only for tutorial purposes####################
#    def example_sha3hetero_2config(self):
#        self.roots= [FireSimSwitchNode()]
#        servers = [FireSimServerNode(server_hardware_config=
#                     "firesim_boom_singlecore_nic_l2_llc4mb_ddr3"),
#                   FireSimServerNode(server_hardware_config=
#                     "firesim_rocket_singlecore_sha3_nic_l2_llc4mb_ddr3")]
#        self.roots[0].add_downlinks(servers)
