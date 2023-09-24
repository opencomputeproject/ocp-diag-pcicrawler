#!/usr/bin/env python3

"""
Copyright (c) Meta Platforms, Inc. and affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import copy
import os
import sys
import socket
from json import dumps
import json
from datetime import datetime
from abc import abstractmethod
import click
from typing import (
    Set,
    List,
    Dict
)
import ocptv.output as tv
from pci_lib import (
    defer_closes,
    get_dmidecode_pci_slots,
    list_devices,
    map_pci_device,
    maybe_shorten_pci_addr,
    PCI_CLASS_MASKS,
    SYSFS_PCI_BUS_DEVICES,
    PCIDevice
)

from pcicrawler.lib.constants import ROOT_UID_REQUIRED

def jsonify(dev, hexify=False, vpd=False, aer=False):
    jd = dev._asdict()
    exptype = dev.express_type
    explink = dev.express_link
    slot = dev.express_slot
    location = dev.location
    del jd["device_name"]
    jd["addr"] = dev.device_name
    if exptype:
        jd["express_type"] = str(exptype)
    if explink:
        jd.update(explink._asdict())
    if slot:
        jd.update(slot._asdict())
    if location:
        jd["location"] = location
    path = [d.device_name for d in dev.get_path()[1:]]
    jd["path"] = path
    if hexify:
        for pad, key in (
            (4, "vendor_id"),
            (4, "device_id"),
            (4, "subsystem_vendor"),
            (4, "subsystem_device"),
            (6, "class_id"),
        ):
            jd[key] = "{:0{pad}x}".format(jd[key], pad=pad)
    if vpd:
        if dev.vpd:
            jd["vpd"] = dev.vpd
    if aer:
        aer_info = dev.express_aer
        if aer_info:
            jd["aer"] = aer_info
    return jd


class ocp_output_obj(object):
    def __init__(self,
                 devs: Set[PCIDevice],
                 json_file: str,
                 ocp_run: tv.TestRun):
        self._json_file = json_file
        self._devs = devs
        self._ocp_run = ocp_run

    class OCPTestStep(object):
        def __init__(self, device, expected_result, ocp_run):
            self._device = device
            self._expected_result = expected_result
            self._ocp_run = ocp_run
            self._error_messages = []

        @abstractmethod
        def run(self, value: str):
            dut = tv.Dut(id=str(self._device), name="PCIe Device")
            with self._ocp_run.scope(dut=dut):
                step = self._ocp_run.add_step(self._test_name)
                if str(value) != str(self._expected_result):
                    step.add_diagnosis(tv.DiagnosisType.FAIL, 
                                    verdict="Test {} for device {}, expected {}, found {}.".format(
                                        self._test_name,
                                        self._device,
                                        self._expected_result,
                                        str(value)))
                else:
                    step.add_diagnosis(tv.DiagnosisType.PASS, 
                                    verdict="Test {} for device {} PASSED".format(
                                        self._test_name,
                                        self._device))


    class check_location(OCPTestStep):
        def __init__(self, 
                     device: PCIDevice, 
                     expected_results: str):
            self._test_name = "pci_location_check"
            super().__init__(device, expected_results)

        def run(self):
            return super().run(self._device.location)


    class check_vendor_id(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_vendor_id_check"
            super().__init__(device, expected_results, ocp_run)

        def run(self):
            return super().run(self._device.vendor_id)


    class check_device_id(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_device_id_check"
            super().__init__(device, expected_results, ocp_run)

        def run(self):
            return super().run(self._device.device_id)


    class check_class_id(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_class_id_check"
            super().__init__(device, expected_results, ocp_run)

        def run(self):
            return super().run(self._device.class_id)


    class check_physical_slot(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_physical_slot_check"
            super().__init__(device, expected_results, ocp_run)

        def run(self):
            return super().run(self._device.express_slot.slot)


    class check_current_link_speed(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_current_link_speed_check"
            super().__init__(device, expected_results, ocp_run)

        def run(self):
            return super().run(self._device.express_link.cur_speed)


    class check_current_link_width(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_current_link_width_check"
            super().__init__(device, expected_results, ocp_run)

        def run(self):
            return super().run(self._device.express_link.cur_width)


    class check_capable_link_speed(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_capable_link_speed_check"
            super().__init__(device, expected_results, ocp_run)
        
        def run(self):
            return super().run(self._device.express_link.capable_speed)


    class check_capable_link_width(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_capable_link_width_check"
            super().__init__(device, expected_results, ocp_run)
        
        def run(self):
            return super().run(self._device.express_link.capable_width)


    class check_address(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_address_check"
            super().__init__(device, expected_results, ocp_run)

        def run(self):
            dut = tv.Dut(id=str(self._device), name="PCIe Device")
            with self._ocp_run.scope(dut=dut):
                step = self._ocp_run.add_step(self._test_name)
                if str(self._device) not in str(self._expected_result):
                    step.add_diagnosis(tv.DiagnosisType.FAIL, 
                                    verdict="Test {} did not find {} in expected list {}.".format(
                                        self._test_name,
                                        self._device,
                                        self._expected_result))
                else:
                    step.add_diagnosis(tv.DiagnosisType.PASS, 
                                    verdict="Test {} for device {} PASSED".format(
                                        self._test_name,
                                        self._device))


    class check_AER(OCPTestStep):
        def __init__(self,
                     device: PCIDevice,
                     expected_results: str,
                     ocp_run: tv.TestRun):
            self._test_name = "pci_AER_check"
            super().__init__(device, expected_results, ocp_run)

        def run(self):
            return super().run(self._device.express_aer)


    class TestSet(object):
        def __init__(self, duts: List[List[PCIDevice]], validate: Dict):
            self.duts = duts
            self.validators = validate

        def __str__(self):
            return str(self.duts)


    def run(self):
        duts = []
        # Process input json
        with open(self._json_file, 'r') as fh:
            try:
                input_json = json.load(fh)
                duts = input_json['duts']
            except Exception as _:
                raise("ERROR: Cannot parse file {}".format(self._json_file))
        filtered_duts = []
        for dut in duts:
            identifier = dut['identifiers']
            potential_duts = list(self._devs)
            # Go look for this device and get the data
            if 'address' in identifier:
                potential_duts = [dut for dut in potential_duts if str(dut) == identifier['address']]
            if 'vendor_id' in identifier:
                potential_duts = [dut for dut in potential_duts if dut.vendor_id == identifier['vendor_id']]
            if 'device_id' in identifier:
                potential_duts = [dut for dut in potential_duts if dut.device_id == identifier['device_id']]
            if len(potential_duts) != 0:
                filtered_duts.append(self.TestSet(potential_duts, dut['validate']))
        # Start checks
        for test_set in filtered_duts:
            # Run checks for each slot
            for dut in test_set.duts:
                if 'vendor_id' in test_set.validators:
                    test = self.check_vendor_id(
                        dut,
                        test_set.validators['vendor_id'],
                        self._ocp_run)
                    self._result = test.run()
                if 'device_id' in test_set.validators:
                    test = self.check_device_id(
                        dut,
                        test_set.validators['device_id'],
                        self._ocp_run)
                    self._result = test.run()
                if 'class_id' in test_set.validators:
                    test = self.check_class_id(
                        dut,
                        test_set.validators['class_id'],
                        self._ocp_run)
                    self._result = test.run()
                if 'physical_slot' in test_set.validators:
                    test = self.check_physical_slot(
                        dut,
                        test_set.validators['physical_slot'],
                        self._ocp_run)
                    self._result = test.run()
                if 'current_link_speed' in test_set.validators:
                    test = self.check_current_link_speed(
                        dut,
                        test_set.validators['current_link_speed'],
                        self._ocp_run)
                    self._result = test.run()
                if 'current_link_width' in test_set.validators:
                    test = self.check_current_link_width(
                        dut, 
                        test_set.validators['current_link_width'], 
                        self._ocp_run)
                    self._result = test.run()
                if 'capable_link_speed' in test_set.validators:
                    test = self.check_capable_link_speed(
                        dut,
                        test_set.validators['capable_link_speed'],
                        self._ocp_run)
                    self._result = test.run()
                if 'capable_link_width' in test_set.validators:
                    test = self.check_capable_link_width(
                        dut,
                        test_set.validators['capable_link_width'],
                        self._ocp_run)
                    self._result = test.run()
                if 'addresses' in test_set.validators:
                    test = self.check_address(
                        dut,
                        test_set.validators['addresses'],
                        self._ocp_run)
                    self._result = test.run()
                if 'check_aer' in test_set.validators:
                    if test_set.validators['check_aer'] == True:
                        test = self.check_AER(
                            dut,
                            None,
                            self._ocp_run)
                        self._result = test.run()


def print_tree_level(devgroups, indent, roots):  # noqa: C901
    spc = indent
    n = 0
    sfx = " \u2502 "
    dmislots = get_dmidecode_pci_slots()
    for dev in roots:
        n += 1
        last = n == len(roots)
        addr = dev.device_name
        dispaddr = maybe_shorten_pci_addr(addr)
        exptype = dev.express_type
        explink = dev.express_link
        slot = dev.express_slot
        dmidev = dmislots.get(addr)
        if last and spc:
            indent = indent[:-3] + "   "
            spc = spc[:-3] + " \u2514\u2500"
        elif spc:
            spc = spc[:-3] + " \u251C\u2500"
        treeline = f'{spc}{click.style(dispaddr, fg="yellow")} '
        if exptype:
            treeline += click.style(str(exptype), fg="red")
        else:
            treeline += click.style("PCI", underline=True)
        if dmidev:
            treeline += ', "{}"'.format(dmidev["designation"])
        if slot:
            treeline += f', slot {click.style(str(slot.slot), fg="blue")}'
            if slot.presence:
                treeline += ", " + click.style("device present", fg="blue")
            if slot.power is not None:
                power = "On" if slot.power else "Off"
                color = "green" if slot.power else "red"
                treeline += ", power: " + click.style(power, fg=color)
            if slot.attn_led != "unsupported" and slot.attn_led != "off":
                treeline += ", attn: " + click.style(slot.attn_led, fg="red")
        if explink:
            if exptype in {"downstream_port", "root_port"} and explink.cur_width != 0:
                treeline += (
                    ", speed "
                    + click.style(explink.cur_speed, fg="blue")
                    + ", width "
                    + click.style(f"x{explink.cur_width}", fg="blue")
                )
            # if cur_width == 0 there's no link at all target_speed is None if
            # device doesn't provide it and ports will report < target speed if
            # their downstream endpoint isn't capable of its target speed, so
            # only check endpoints.
            if (
                explink.target_speed
                and explink.cur_speed != explink.target_speed
                and exptype == "endpoint"
                and explink.cur_width != 0
            ):
                treeline += (
                    ", current speed "
                    + click.style(explink.cur_speed, fg="blue")
                    + " target speed "
                    + click.style(explink.target_speed, fg="blue")
                )
        if (
            exptype in {"endpoint", "upstream_port", "root_complex_endpoint"}
            or exptype is None
        ):
            treeline += ", " + click.style(dev.name, fg="green")
        click.echo(treeline)
        if addr in devgroups:
            print_tree_level(devgroups, indent + sfx, devgroups[addr])


def print_tree(devs):
    roots = []
    devgroups = {}
    for dev in devs:
        parent = dev.parent
        if parent:
            parentid = parent.device_name
            if parentid in devgroups:
                devgroups[parentid].append(dev)
            else:
                devgroups[parentid] = [dev]
        # Only find devices under a root port (don't display built-in "devices"
        # in the tree view)
        elif dev.express_type == "root_port":
            roots.append(dev)
    print_tree_level(devgroups, "", roots)


def no_scripting():
    if not sys.stdout.isatty():
        warning = (
            "It looks like you may be writing a script that uses pcicrawler. "
            "Please always use the --json flag from scripts, do NOT parse "
            "output intended for humans!"
        )
        print(warning)
        print(warning, file=sys.stderr)


def is_physfn(device):
    """
    When device is SR-IOV enabled, there could be 0 or more virtual
    functions (VF) for each physical function (PF).
    Existence of /sys/bus/pci/devices/<bdf>/phsyfn confirms device is VF
    while /sys/bus/pci/devices/<bdf>/virtfn* or no such file indicates PF
    """
    path = SYSFS_PCI_BUS_DEVICES + device.device_name + "/physfn"
    return not os.path.exists(path)


@click.command()  # noqa: C901
@click.option("--json/--no-json", "-j", default=False, help="Output in JSON format")
@click.option(
    "--ocp",
    "-o",
    default=None,
    help="Run pcicrawler as an OCP diag. "
    "Requires an input JSON file as an argument.",
)
@click.option(
    "--hexify/--no-hexify",
    "-x",
    default=False,
    help="Output vendor/device/class IDs as hex "
    "strings instead of numbers in JSON output",
)
@click.option(
    "--aer/--no-aer",
    "-a",
    default=False,
    help="Include PCIe Advanced Error Reporting (AER) information "
    "when available - only provided in JSON output",
)
@click.option("--tree/--no-tree", "-t", default=False, help="Output as a tree")
@click.option(
    "--device",
    "-d",
    default=None,
    help="Only show devices matching this PCI vendor/device ID, "
    "(syntax like vendor:device, or vendor:, in hex)",
)
@click.option(
    "--class-id",
    "-c",
    default=None,
    help="Only show devices matching this PCI class ID in hex, "
    "or one of: " + ", ".join(PCI_CLASS_MASKS.keys()),
)
@click.option("--addr", "-s", default=None, help="Show device with this PCI address")
@click.option(
    "--include-path/--no-include-path",
    "-p",
    default=False,
    help="Include devices upstream of matched devices",
)
@click.option(
    "--express-only/--no-express-only",
    "-e",
    default=False,
    help="Only show PCIe devices",
)
@click.option(
    "--vpd/--no-vpd",
    "-V",
    default=False,
    help="Include VPD data if present, does not work with --tree",
)
@click.option(
    "--physfn-only",
    is_flag=True,
    default=False,
    help="Show only PFs if SR-IOV is enabled",
)
@click.option(
    "--no-builtin",
    is_flag=True,
    default=False,
    help="Exclude builtin root devices (defaults to true with --tree)",
)
@click.option(
    "--verbose/--no-verbose",
    "-v",
    default=False,
    help="Show debugging output - not compatible with JSON/tree views",
)
def main(
    json,
    ocp,
    hexify,
    aer,
    tree,
    device,
    class_id,
    addr,
    include_path,
    express_only,
    vpd,
    physfn_only,
    no_builtin,
    verbose,
):
    """
    Tool to display/filter/export information about PCI or PCI Express devices,
    as well as their topology.

    Must run as root as it uses privileged sysfs entries.
    """
    if os.geteuid() != 0:
        print("error: pcicrawler must be run as root.", file=sys.stderr)
        sys.exit(ROOT_UID_REQUIRED)

    vid = None
    did = None
    devs = []
    if device:
        try:
            vid, did = device.split(":")
            vid = int(vid, 16)
            if did:
                did = int(did, 16)
            else:
                did = None
        except Exception as e:
            raise click.ClickException(f"Could not parse vendor/device id: {e}")
    if class_id:
        if class_id in PCI_CLASS_MASKS:
            class_id, class_mask = PCI_CLASS_MASKS[class_id]
        else:
            class_id, class_mask = int(class_id, 16), 0xFFFFFF
    if addr:
        dev = map_pci_device(addr)
        if not dev:
            raise click.ClickException(f"Could not open PCI dev {addr}")
        devs = [dev]
    else:
        devs = list_devices()

    if vid:
        devs = filter(lambda d: d.vendor_id == vid, devs)
    if did:
        devs = filter(lambda d: d.device_id == did, devs)
    if class_id:
        devs = filter(lambda d: d.class_id & class_mask == class_id, devs)
    if express_only:
        devs = filter(lambda d: d.express_link is not None, devs)
    if no_builtin:
        devs = filter(lambda d: d.parent or d.express_type == "root_port", devs)
    if physfn_only:
        devs = filter(is_physfn, devs)

    devs = set(devs)
    if include_path or tree:
        for dev in devs.copy():
            devs |= set(dev.get_path())

    if tree:
        no_scripting()
        # When asked to print a tree, include filtered devices parents
        print_tree(sorted(devs, key=lambda d: d.device_name))
    elif ocp:
        run = tv.TestRun(name="pcicrawler", version="")
        ocp_obj = ocp_output_obj(devs, ocp, run)
        ocp_obj.run()
    elif json:
        jdevs = {}
        for dev in devs:
            addr = dev.device_name
            jdevs[addr] = copy.deepcopy(jsonify(dev, hexify=hexify, vpd=vpd, aer=aer))
        click.echo(dumps(jdevs))
    else:
        no_scripting()
        for dev in devs:
            location = dev.location
            exptype = dev.express_type

            line = click.style(str(dev), fg="yellow") + ", "
            if exptype:
                line += f'PCIe {click.style(str(exptype), fg="red")}, '
            line += click.style(dev.name, fg="green")
            click.echo(line)
            if location:
                click.echo(f"  connected via: {click.style(location, bold=True)}")
            if verbose:
                debugging_data = dev.get_debugging_details()
                click.echo(f"  debug: {debugging_data}")
            if vpd:
                if dev.vpd:
                    ident = dev.vpd["identifier_string"]
                    if ident:
                        click.echo(
                            "  VPD Identifier: " f"{click.style(ident, bold=True)}"
                        )
                    for k, v in dev.vpd["fields"].items():
                        click.echo(
                            f'    {click.style(k, fg="blue")}='
                            f"{click.style(v, bold=True)}"
                        )


if __name__ == "__main__":
    with defer_closes():
        main()
