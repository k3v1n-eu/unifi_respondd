#!/usr/bin/env python3

import socket
import struct
import json
import zlib
import time

import dataclasses
from dataclasses_json import dataclass_json
import unifi_respondd


@dataclasses.dataclass
class FirmwareInfo:
    base: str
    release: str


@dataclasses.dataclass
class LocationInfo:
    latitude: float
    longitude: float


@dataclasses.dataclass
class HardwareInfo:
    model: str

@dataclasses.dataclass
class OwnerInfo:
    contact: str

@dataclass_json
@dataclasses.dataclass
class NodeInfo:
    firmware: FirmwareInfo
    hostname: str
    node_id: str
    location: LocationInfo
    hardware: HardwareInfo
    owner: OwnerInfo


@dataclasses.dataclass
class ClientInfo:
    total: int
    wifi: int


@dataclasses.dataclass
class MemoryInfo:
    total: int
    free: int
    buffers: int


@dataclass_json
@dataclasses.dataclass
class StatisticsInfo:
    clients: ClientInfo
    uptime: int
    node_id: str
    loadavg: float
    memory: MemoryInfo


class ResponddClient:
    def __init__(self, config):
        self._config = config
        self._sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)

    @property
    def _aps(self):
        return unifi_respondd.get_infos()

    @property
    def _nodeinfos(self):
        return self.getNodeInfos()

    @property
    def _statistics(self):
        return self.getStatistics()

    @staticmethod
    def joinMCAST(sock, addr, ifname):
        group = socket.inet_pton(socket.AF_INET6, addr)
        if_idx = socket.if_nametoindex(ifname)
        sock.setsockopt(
            socket.IPPROTO_IPV6,
            socket.IPV6_JOIN_GROUP,
            group + struct.pack("I", if_idx),
        )

    def getNodeInfos(self):
        aps = self._aps
        nodes = []
        for ap in aps.accesspoints:
            nodes.append(
                NodeInfo(
                    firmware=FirmwareInfo(base=ap.firmware, release=""),
                    hostname=ap.name,
                    node_id=ap.mac.replace(":", ""),
                    location=LocationInfo(latitude=ap.latitude, longitude=ap.longitude),
                    hardware=HardwareInfo(model=ap.model),
                    owner=OwnerInfo(contact=ap.contact),
                )
            )
        return nodes

    def getStatistics(self):
        aps = self._aps
        statistics = []
        for ap in aps.accesspoints:
            statistics.append(
                StatisticsInfo(
                    clients=ClientInfo(total=ap.client_count, wifi=ap.client_count),
                    uptime=ap.uptime,
                    node_id=ap.mac.replace(":", ""),
                    loadavg=ap.load_avg,
                    memory=MemoryInfo(
                        total=int(ap.mem_total / 1024),
                        free=int((ap.mem_total - ap.mem_used) / 1024),
                        buffers=int(ap.mem_buffer / 1024),
                    ),
                )
            )
        return statistics

    def start(self):
        self._sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_BINDTODEVICE,
            bytes(self._config.interface.encode()),
        )
        self._sock.bind(("::", self._config.multicast_port))

        self.joinMCAST(
            self._sock, self._config.multicast_address, self._config.interface
        )

        while True:
            msg, sourceAddress = self._sock.recvfrom(2048)

            msgSplit = str(msg, "UTF-8").split(" ")

            responseStruct = {}
            if msgSplit[0] == "GET":  # multi_request
                for request in msgSplit[1:]:
                    responseStruct[request] = self.buildStruct(request)
                self.sendStruct(sourceAddress, responseStruct, True)
            else:  # single_request
                responseStruct = self.buildStruct(msgSplit[0])
                self.sendStruct(sourceAddress, responseStruct, False)

    def merge_node(self, responseStruct):
        merged = {}
        for key in responseStruct.keys():
            print(key)
            if responseStruct[key]:
                for info in responseStruct[key]:
                    if info.node_id not in merged:
                        merged[info.node_id] = {key: info}
                    else:
                        merged[info.node_id].update({key: info})
        return merged

    def buildStruct(self, responseType):

        responseClass = None
        if responseType == "statistics":
            responseClass = self._statistics
        elif responseType == "nodeinfo":
            responseClass = self._nodeinfos
        else:
            print("unknown command: " + responseType)
            return

        return responseClass

    def sendStruct(self, destAddress, responseStruct, withCompression):
        if self._config.verbose:
            print(
                "%14.3f %35s %5d: " % (time.time(), destAddress[0], destAddress[1]),
                end="",
            )
            print(responseStruct)
        merged = self.merge_node(responseStruct)
        for infos in merged.values():
            node = {}
            for key, info in infos.items():
                node.update({key: info.to_dict()})
            responseData = bytes(json.dumps(node), "UTF-8")
            print(responseData)

            if withCompression:
                encoder = zlib.compressobj(
                    zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15
                )  # The data may be decompressed using zlib and many zlib bindings using -15 as the window size parameter.
                responseData = encoder.compress(responseData)
                responseData += encoder.flush()
                # return compress(str.encode(json.dumps(ret)))[2:-4] # bug? (mesh-announce strip here)

            self._sock.sendto(responseData, destAddress)
