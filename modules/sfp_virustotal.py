# -*- coding: utf-8 -*-
# -------------------------------------------------------------------------------
# Name:         sfp_virustotal
# Purpose:      Query VirusTotal for identified IP addresses.
#
# Author:      Steve Micallef <steve@binarypool.com>
#
# Created:     21/03/2014
# Copyright:   (c) Steve Micallef
# Licence:     GPL
# -------------------------------------------------------------------------------

import json
import time

from netaddr import IPNetwork

from spiderfoot import SpiderFootEvent, SpiderFootPlugin


class sfp_virustotal(SpiderFootPlugin):

    meta = {
        'name': "VirusTotal",
        'summary': "Obtain information from VirusTotal about identified IP addresses.",
        'flags': ["apikey"],
        'useCases': ["Investigate", "Passive"],
        'categories': ["Reputation Systems"],
        'dataSource': {
            'website': "https://www.virustotal.com/",
            'model': "FREE_AUTH_LIMITED",
            'references': [
                "https://developers.virustotal.com/v3.0/reference"
            ],
            'apiKeyInstructions': [
                "Visit https://www.virustotal.com/",
                "Register a free account",
                "Click on your profile",
                "Click on API Key",
                "The API key is listed under 'API Key'"
            ],
            'favIcon': "https://www.virustotal.com/gui/images/favicon.png",
            'logo': "https://www.virustotal.com/gui/images/logo.svg",
            'description': "Analyze suspicious files and URLs to detect types of malware, "
                                "automatically share them with the security community.",
        }
    }

    # Default options
    opts = {
        'api_key': '',
        'verify': True,
        'publicapi': True,
        'checkcohosts': True,
        'checkaffiliates': True,
        'netblocklookup': True,
        'maxnetblock': 24,
        'subnetlookup': True,
        'maxsubnet': 24
    }

    # Option descriptions
    optdescs = {
        'api_key': 'VirusTotal API Key.',
        'publicapi': 'Are you using a public key? If so SpiderFoot will pause for 15 seconds after each query to avoid VirusTotal dropping requests.',
        'checkcohosts': 'Check co-hosted sites?',
        'checkaffiliates': 'Check affiliates?',
        'netblocklookup': 'Look up all IPs on netblocks deemed to be owned by your target for possible hosts on the same target subdomain/domain?',
        'maxnetblock': 'If looking up owned netblocks, the maximum netblock size to look up all IPs within (CIDR value, 24 = /24, 16 = /16, etc.)',
        'subnetlookup': 'Look up all IPs on subnets which your target is a part of?',
        'maxsubnet': 'If looking up subnets, the maximum subnet size to look up all the IPs within (CIDR value, 24 = /24, 16 = /16, etc.)',
        'verify': 'Verify that any hostnames found on the target domain still resolve?'
    }

    # Be sure to completely clear any class variables in setup()
    # or you run the risk of data persisting between scan runs.

    results = None
    errorState = False

    def setup(self, sfc, userOpts=dict()):
        self.sf = sfc
        self.results = self.tempStorage()

        # Clear / reset any other class member variables here
        # or you risk them persisting between threads.

        for opt in list(userOpts.keys()):
            self.opts[opt] = userOpts[opt]

    # What events is this module interested in for input
    def watchedEvents(self):
        return ["IP_ADDRESS", "AFFILIATE_IPADDR", "INTERNET_NAME",
                "CO_HOSTED_SITE", "NETBLOCK_OWNER",
                "NETBLOCK_MEMBER"]

    # What events this module produces
    def producedEvents(self):
        return ["MALICIOUS_IPADDR", "MALICIOUS_INTERNET_NAME",
                "MALICIOUS_COHOST", "MALICIOUS_AFFILIATE_INTERNET_NAME",
                "MALICIOUS_AFFILIATE_IPADDR", "MALICIOUS_NETBLOCK",
                "MALICIOUS_SUBNET", "INTERNET_NAME", "AFFILIATE_INTERNET_NAME",
                "INTERNET_NAME_UNRESOLVED", 'DOMAIN_NAME']

    def query(self, qry):
        ret = None

        if self.sf.validIP(qry):
            url = "https://www.virustotal.com/vtapi/v2/ip-address/report?ip=" + qry
        else:
            url = "https://www.virustotal.com/vtapi/v2/domain/report?domain=" + qry

        res = self.sf.fetchUrl(url + "&apikey=" + self.opts['api_key'],
                               timeout=self.opts['_fetchtimeout'], useragent="SpiderFoot")

        # Public API is limited to 4 queries per minute
        if self.opts['publicapi']:
            time.sleep(15)

        if res['content'] is None:
            self.sf.info("No VirusTotal info found for " + qry)
            return None

        try:
            ret = json.loads(res['content'])
        except Exception as e:
            self.sf.error(f"Error processing JSON response from VirusTotal: {e}", False)
            self.errorState = True
            return None

        return ret

    # Handle events sent to this module
    def handleEvent(self, event):
        eventName = event.eventType
        srcModuleName = event.module
        eventData = event.data

        if self.errorState:
            return None

        self.sf.debug(f"Received event, {eventName}, from {srcModuleName}")

        if self.opts['api_key'] == "":
            self.sf.error("You enabled sfp_virustotal but did not set an API key!", False)
            self.errorState = True
            return None

        # Don't look up stuff twice
        if eventData in self.results:
            self.sf.debug(f"Skipping {eventData}, already checked.")
            return None
        else:
            self.results[eventData] = True

        if eventName.startswith("AFFILIATE") and not self.opts['checkaffiliates']:
            return None

        if eventName == 'CO_HOSTED_SITE' and not self.opts['checkcohosts']:
            return None

        if eventName == 'NETBLOCK_OWNER':
            if not self.opts['netblocklookup']:
                return None
            else:
                if IPNetwork(eventData).prefixlen < self.opts['maxnetblock']:
                    self.sf.debug("Network size bigger than permitted: "
                                  + str(IPNetwork(eventData).prefixlen) + " > "
                                  + str(self.opts['maxnetblock']))
                    return None

        if eventName == 'NETBLOCK_MEMBER':
            if not self.opts['subnetlookup']:
                return None
            else:
                if IPNetwork(eventData).prefixlen < self.opts['maxsubnet']:
                    self.sf.debug("Network size bigger than permitted: "
                                  + str(IPNetwork(eventData).prefixlen) + " > "
                                  + str(self.opts['maxsubnet']))
                    return None

        qrylist = list()
        if eventName.startswith("NETBLOCK_"):
            for ipaddr in IPNetwork(eventData):
                qrylist.append(str(ipaddr))
                self.results[str(ipaddr)] = True
        else:
            qrylist.append(eventData)

        for addr in qrylist:
            if self.checkForStop():
                return None

            info = self.query(addr)
            if info is None:
                continue
            if len(info.get('detected_urls', [])) > 0:
                self.sf.info("Found VirusTotal URL data for " + addr)
                if eventName in ["IP_ADDRESS"] or eventName.startswith("NETBLOCK_"):
                    evt = "MALICIOUS_IPADDR"
                    infotype = "ip-address"

                if eventName == "AFFILIATE_IPADDR":
                    evt = "MALICIOUS_AFFILIATE_IPADDR"
                    infotype = "ip-address"

                if eventName == "INTERNET_NAME":
                    evt = "MALICIOUS_INTERNET_NAME"
                    infotype = "domain"

                if eventName == "AFFILIATE_INTERNET_NAME":
                    evt = "MALICIOUS_AFFILIATE_INTERNET_NAME"
                    infotype = "domain"

                if eventName == "CO_HOSTED_SITE":
                    evt = "MALICIOUS_COHOST"
                    infotype = "domain"

                infourl = "<SFURL>https://www.virustotal.com/en/" + infotype + "/" + \
                          addr + "/information/</SFURL>"

                # Notify other modules of what you've found
                e = SpiderFootEvent(evt, "VirusTotal [" + addr + "]\n" + infourl, self.__name__, event)
                self.notifyListeners(e)

            # Treat siblings as affiliates if they are of the original target, otherwise
            # they are additional hosts within the target.
            if 'domain_siblings' in info:
                if eventName in ["IP_ADDRESS", "INTERNET_NAME"]:
                    for s in info['domain_siblings']:
                        if self.getTarget().matches(s):
                            if s not in self.results:
                                if self.opts['verify']:
                                    if not self.sf.resolveHost(s):
                                        e = SpiderFootEvent("INTERNET_NAME_UNRESOLVED", s, self.__name__, event)
                                        self.notifyListeners(e)
                                    else:
                                        e = SpiderFootEvent("INTERNET_NAME", s, self.__name__, event)
                                        self.notifyListeners(e)

                                if self.sf.isDomain(s, self.opts['_internettlds']):
                                    e = SpiderFootEvent("DOMAIN_NAME", s, self.__name__, event)
                                    self.notifyListeners(e)
                        else:
                            if s not in self.results:
                                e = SpiderFootEvent("AFFILIATE_INTERNET_NAME", s, self.__name__, event)
                                self.notifyListeners(e)

            if 'subdomains' in info and eventName == "INTERNET_NAME":
                for n in info['subdomains']:
                    if n not in self.results:
                        if self.opts['verify']:
                            if not self.sf.resolveHost(n):
                                e = SpiderFootEvent("INTERNET_NAME_UNRESOLVED", n, self.__name__, event)
                                self.notifyListeners(e)
                        else:
                            e = SpiderFootEvent("INTERNET_NAME", n, self.__name__, event)
                            self.notifyListeners(e)

                        if self.sf.isDomain(n, self.opts['_internettlds']):
                            e = SpiderFootEvent("DOMAIN_NAME", n, self.__name__, event)
                            self.notifyListeners(e)

# End of sfp_virustotal class
