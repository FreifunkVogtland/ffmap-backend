from collections import Counter, defaultdict
from datetime import datetime
from functools import reduce


def build_mac_table(nodes):
    macs = dict()
    for node_id, node in nodes.items():
        try:
            for mac in node['nodeinfo']['network']['mesh_interfaces']:
                macs[mac] = node_id
        except KeyError:
            pass

        try:
            for upper_if in node['nodeinfo']['network']['mesh'].values():
                for lower_if in upper_if['interfaces'].values():
                    for mac in lower_if:
                        macs[mac] = node_id
        except KeyError:
            pass

    return macs


def prune_nodes(nodes, now, days):
    prune = []
    for node_id, node in nodes.items():
        if 'lastseen' not in node:
            prune.append(node_id)
            continue

        lastseen = datetime.strptime(node['lastseen'], '%Y-%m-%dT%H:%M:%S')
        delta = (now - lastseen).days

        if delta >= days:
            prune.append(node_id)

    for node_id in prune:
        del nodes[node_id]


def prune_node(nodes, nodeid):
    if nodeid in nodes:
        del nodes[nodeid]


def mark_online(node, now):
    node['lastseen'] = now.isoformat()
    node.setdefault('firstseen', now.isoformat())
    node['flags']['online'] = True


def check_uplink(group):
    peers = []
    for peer in group['peers']:
        if group['peers'][peer] and "established" in group['peers'][peer]:
            peers.append(peer)

    return peers


def check_uplink_recursive(groups):
    peers = []

    for group in groups.values():
        peers.extend(check_uplink(group))

        if 'groups' in group:
            peers.extend(check_uplink_recursive(group['groups']))

    return peers


def mark_uplink(node, stats):
    try:
        peers = check_uplink_recursive(stats['mesh_vpn']['groups'])

        if peers:
            node['flags']['uplink'] = True
            node['statistics']['vpn_peers'] = peers
    except KeyError:
        pass


def import_nodeinfo(nodes, nodeinfos, now, assume_online=False):
    for nodeinfo in filter(lambda d: 'node_id' in d, nodeinfos):
        node = nodes.setdefault(nodeinfo['node_id'], {'flags': dict()})
        node['nodeinfo'] = nodeinfo
        node['flags']['online'] = False
        node['flags']['uplink'] = False

        if assume_online:
            mark_online(node, now)


def reset_statistics(nodes):
    for node in nodes.values():
        node['statistics'] = {'clients': 0}


def import_statistics(nodes, stats):
    def add(node, statistics, target, source, f=lambda d: d):
        try:
            node['statistics'][target] = f(reduce(dict.__getitem__,
                                                  source,
                                                  statistics))
        except (KeyError, TypeError, ZeroDivisionError):
            pass

    macs = build_mac_table(nodes)
    stats = filter(lambda d: 'node_id' in d, stats)
    stats = filter(lambda d: d['node_id'] in nodes, stats)
    for node, stats in map(lambda d: (nodes[d['node_id']], d), stats):
        add(node, stats, 'clients', ['clients', 'total'])
        add(node, stats, 'uptime', ['uptime'])
        add(node, stats, 'loadavg', ['loadavg'])
        add(node, stats, 'memory_usage', ['memory'],
            lambda d: 1 - (d['free'] + d['buffers'] + d['cached']) / d['total'])
        add(node, stats, 'rootfs_usage', ['rootfs_usage'])
        add(node, stats, 'traffic', ['traffic'])
        mark_uplink(node, stats)


def import_mesh_ifs_vis_data(nodes, vis_data):
    macs = build_mac_table(nodes)

    mesh_ifs = defaultdict(lambda: set())
    for line in filter(lambda d: 'secondary' in d, vis_data):
        primary = line['of']
        mesh_ifs[primary].add(primary)
        mesh_ifs[primary].add(line['secondary'])

    def if_to_node(ifs):
        a = filter(lambda d: d in macs, ifs)
        a = map(lambda d: nodes[macs[d]], a)
        try:
            return next(a), ifs
        except StopIteration:
            return None

    mesh_nodes = filter(lambda d: d, map(if_to_node, mesh_ifs.values()))

    for v in mesh_nodes:
        node = v[0]

        ifs = set()

        try:
            ifs = ifs.union(set(node['nodeinfo']['network']['mesh_interfaces']))
        except KeyError:
            pass

        try:
            ifs = ifs.union(set(node['nodeinfo']['network']['mesh']['bat0']['interfaces']['wireless']))
        except KeyError:
            pass

        try:
            ifs = ifs.union(set(node['nodeinfo']['network']['mesh']['bat0']['interfaces']['tunnel']))
        except KeyError:
            pass

        try:
            ifs = ifs.union(set(node['nodeinfo']['network']['mesh']['bat0']['interfaces']['other']))
        except KeyError:
            pass

        node['nodeinfo']['network']['mesh_interfaces'] = list(ifs | v[1])


def import_vis_clientcount(nodes, vis_data):
    macs = build_mac_table(nodes)
    data = filter(lambda d: d.get('label', None) == 'TT', vis_data)
    data = filter(lambda d: d['router'] in macs, data)
    data = map(lambda d: macs[d['router']], data)

    for node_id, clientcount in Counter(data).items():
        nodes[node_id]['statistics'].setdefault('clients', clientcount)


def mark_vis_data_online(nodes, vis_data, now):
    macs = build_mac_table(nodes)

    online = set()
    for line in vis_data:
        if 'primary' in line:
            online.add(line['primary'])
        elif 'secondary' in line:
            online.add(line['secondary'])
        elif 'gateway' in line:
            # This matches clients' MACs.
            # On pre-Gluon nodes the primary MAC will be one of it.
            online.add(line['gateway'])

    for mac in filter(lambda d: d in macs, online):
        mark_online(nodes[macs[mac]], now)
