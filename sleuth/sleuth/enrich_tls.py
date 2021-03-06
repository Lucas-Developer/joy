"""
 *
 * Copyright (c) 2017 Cisco Systems, Inc.
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 *   Redistributions of source code must retain the above copyright
 *   notice, this list of conditions and the following disclaimer.
 *
 *   Redistributions in binary form must reproduce the above
 *   copyright notice, this list of conditions and the following
 *   disclaimer in the documentation and/or other materials provided
 *   with the distribution.
 *
 *   Neither the name of the Cisco Systems, Inc. nor the names of its
 *   contributors may be used to endorse or promote products derived
 *   from this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
 * FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * COPYRIGHT HOLDERS OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
 * INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
 * HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
 * STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
 * OF THE POSSIBILITY OF SUCH DAMAGE.
 *
"""
import os
import json

UNKNOWN = 0
INVALID = 1
VALID = 2


class Policy:
    "Class to import seclevel policy"

    def __init__(self, filename, failure_threshold):
        cur_dir = os.path.dirname(__file__)
        policy_path = os.path.join(cur_dir, filename)
        with open(policy_path) as f:
            policy_data = json.load(f)
            self.classifications = policy_data["classification"]
            self.rules = policy_data["rules"]
            self.failure_threshold = int(failure_threshold) if failure_threshold else policy_data["default_failure_threshold"]

    def seclevel(self, x):
        for classification, value in self.classifications.iteritems():
            if x == value:
                return classification
        return "unknown"


# It should be noted that this is a soft check on whether or not the selected
# cipher suite would be acceptable to the specified compliance policy
#
def check_compliance(compliance_policy, scs):
    if not scs:
        return 'unknown'

    if not compliance_policy:
        return 'unknown'

    cur_dir = os.path.dirname(__file__)
    check_data_file = "data_tls_params.json"
    check_data_path = os.path.join(cur_dir, check_data_file)

    compliance_data_file = "compliance.json"
    compliance_data_path = os.path.join(cur_dir, compliance_data_file)

    with open(check_data_path) as check_f:
        with open(compliance_data_path) as compliance_f:
            check_data = json.load(check_f)
            compliance_data = json.load(compliance_f)

            if compliance_policy not in compliance_data:
                return "unknown - undefined policy"
            else:
                scs_desc = check_data["tls_params"][scs]["desc"]
                return "yes" if scs_desc in compliance_data[compliance_policy] else "no"

    return "error loading file"


def tls_seclevel(policy, unknowns, scs, client_key_length, certs):
    if not scs:
        return 'unknown'

    cur_dir = os.path.dirname(__file__)
    data_file = "data_tls_params.json"
    data_path = os.path.join(cur_dir, data_file)

    with open(data_path) as f:
        data = json.load(f)
        params = data["tls_params"][scs]

        policy_rules = policy.rules
        min_seclevel = min(policy.classifications.values())
        max_seclevel = max(policy.classifications.values())

        # Analyze key exchange seclevel based on key length seclevel information
        # provided in the policy json
        #
        kex = params['kex']
        kex_policy = policy_rules["kex"]

        kex_seclevel = UNKNOWN
        if kex in kex_policy:
            if client_key_length and "client_key_length" in kex_policy[kex]:
                kex_seclevel = min_seclevel
                for each in kex_policy[kex]["client_key_length"]:
                    if client_key_length >= int(each):
                        kex_seclevel = kex_policy[kex]["client_key_length"][each]
            elif "default" in kex_policy[kex]:
                kex_seclevel = kex_policy[kex]["default"]


        # Analyze seclevel of certificates based on algorithm and key size seclevel
        # information given in the policy json
        #
        if certs:
            certs_seclevel = max_seclevel
            sig_policy = policy_rules["sig_alg"]

            for x in certs:
                sig_alg = x['signature_algorithm']
                sig_key_size = x['signature_key_size']

                if sig_alg and sig_alg in sig_policy:
                    if sig_key_size and "sig_key_size" in sig_policy[sig_alg]:
                        for each in sig_policy[sig_alg]["sig_key_size"]:
                            if sig_key_size >= int(each):
                                tmp_seclevel = sig_policy[sig_alg]["sig_key_size"][each]
                    elif "seclevel" in sig_policy[sig_alg]:
                        tmp_seclevel = sig_policy[sig_alg]["seclevel"]
                else:
                    tmp_seclevel = UNKNOWN

                if tmp_seclevel and tmp_seclevel < certs_seclevel:
                    certs_seclevel = tmp_seclevel
        else:
            certs_seclevel = UNKNOWN

        # Other seclevel items can be assessed in one step due to the lack of nested
        # seclevel elements; we create an inventory for the flow here
        #
        seclevel_inventory = {
            "kex": kex_seclevel,
            "certs": certs_seclevel,
            "sig": policy_rules["sig"][params['sig']] if params['sig'] in policy_rules["sig"] else UNKNOWN,
            "enc": policy_rules["enc"][params['enc']] if params['enc'] in policy_rules["enc"] else UNKNOWN,
            "auth": policy_rules["auth"][params['auth']] if params['auth'] in policy_rules["auth"] else UNKNOWN,
            "hash": policy_rules["hash"][params['hash']] if params['hash'] in policy_rules["hash"] else UNKNOWN
        }

        # fetch min seclevel element based on whether or not 'unknown' elements should be reported.
        # default here is 'report'
        #
        if unknowns == "ignore":
            seclevel_floor = min([ v for k, v in seclevel_inventory.iteritems() if v != UNKNOWN])
        else:
            seclevel_floor = min(seclevel_inventory.values())

        classification = policy.seclevel(seclevel_floor)

        if seclevel_floor <= policy.failure_threshold:
            reasons = [item[0] for item in seclevel_inventory.items() if item[1] == seclevel_floor]
            return { "classification": classification,
                     "reason": reasons }

        return classification


def enrich_tls(flow, kwargs):
    if 'tls' not in flow:
        return None
    else:
        # Get security-relevant parameters from flow record
        tls = flow['tls']

        if 'tls_client_key_length' in tls:
            # Subtract 16 encoding bits
            client_key_length = tls['tls_client_key_length'] - 16
        else:
            client_key_length = None

        if 's_tls_ext' in tls:
            server_extensions = tls['s_tls_ext']
        else:
            server_extensions = None

        if 'scs' in tls:
            scs = tls['scs']
        else:
            scs = None

        if 'server_cert' in tls:
            certs = list()
            for x in tls['server_cert']:
                tmp = dict()
                tmp['signature_algorithm'] = x['signature_algorithm']
                tmp['signature_key_size'] = x['signature_key_size']
                certs.append(tmp)
        else:
            certs = None

        seclevel_policy = Policy(kwargs["policy_file"], kwargs["failure_threshold"])

        tls_sec_info = {}
        tls_sec_info["seclevel"] = tls_seclevel(seclevel_policy, kwargs["unknowns"], scs, client_key_length, certs)

        # if compliance argument was passed, assess and add to tls_sec_info object.
        # It should be noted that this is a soft check on whether or not the selected
        # cipher suite would be acceptable to the specified compliance policy
        #
        if kwargs["compliance"]:
            for policy in kwargs["compliance"]:
                tls_sec_info[policy + "_compliant"] = check_compliance(policy, scs)

        return tls_sec_info
