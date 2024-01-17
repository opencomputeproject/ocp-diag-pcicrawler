#!/usr/bin/env python3

"""
Copyright (c) Meta Platforms, Inc. and affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

import json
import socket
import pkg_resources
import typing as ty
import ocptv.output as tv
from dataclasses import dataclass
from pci_lib import PCIDevice
from abc import ABCMeta, abstractmethod


class OCPTestStep(metaclass=ABCMeta):
    def __init__(self,
                    device: PCIDevice,
                    expected_result: str,
                    ocp_run: tv.TestRun):
        self._device = device
        self._expected_result = expected_result
        self._ocp_run = ocp_run

    @property
    @abstractmethod
    def _test_name(self):
        pass

    @abstractmethod
    def run(self, current_value: str):
        step = self._ocp_run.add_step(self._test_name)
        if str(current_value) != str(self._expected_result):
            step.add_diagnosis(tv.DiagnosisType.FAIL,
                            verdict="Test {} for device {}, expected {}, found {}.".format(
                                self._test_name,
                                self._device,
                                self._expected_result,
                                str(current_value)))
            return False
        step.add_diagnosis(tv.DiagnosisType.PASS,
                        verdict="Test {} for device {} PASSED".format(
                            self._test_name,
                            self._device))
        return True


class CheckLocation(OCPTestStep):
    _test_name = "pci_location_check"

    def run(self):
        return super().run(self._device.location)


class CheckVendorID(OCPTestStep):
    _test_name = "pci_vendor_id_check"

    def run(self):
        return super().run(self._device.vendor_id)


class CheckDeviceID(OCPTestStep):
    _test_name = "pci_device_id_check"

    def run(self):
        return super().run(self._device.device_id)


class CheckClassID(OCPTestStep):
    _test_name = "pci_class_id_check"

    def run(self):
        return super().run(self._device.class_id)


class CheckPhysicalSlot(OCPTestStep):
    _test_name = "pci_physical_slot_check"

    def run(self):
       if self._device.express_slot is None:
           return False  # can also trigger skip if there's an input param saying that it's acceptable to run this against a simple pci device (per the discussion last week about step skip semantics)
       return super().run(self._device.express_slot.slot)


class CheckCurrentLinkSpeed(OCPTestStep):
    _test_name = "pci_current_link_speed_check"

    def run(self):
        return super().run(self._device.express_link.cur_speed)


class CheckCurrentLinkWidth(OCPTestStep):
    _test_name = "pci_current_link_width_check"

    def run(self):
        return super().run(self._device.express_link.cur_width)


class CheckCapableLinkSpeed(OCPTestStep):
    _test_name = "pci_capable_link_speed_check"

    def run(self):
        return super().run(self._device.express_link.capable_speed)


class CheckCapableLinkWidth(OCPTestStep):
    _test_name = "pci_capable_link_width_check"

    def run(self):
        return super().run(self._device.express_link.capable_width)


class CheckAddress(OCPTestStep):
    _test_name = "pci_address_check"

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
                return False
            step.add_diagnosis(tv.DiagnosisType.PASS,
                            verdict="Test {} for device {} PASSED".format(
                                self._test_name,
                                self._device))
            return True


class CheckAER(OCPTestStep):
    _test_name = "pci_AER_check"

    def __init__(self,
                device: PCIDevice,
                expected_results: str,
                ocp_run: tv.TestRun):
        super().__init__(device, expected_results, ocp_run)

    def run(self):
        return super().run(self._device.express_aer)


@dataclass
class TestSet:
    '''Object to hold a list of DUTs to be tested and
    the values they need to be compared against.'''
    duts: list
    conditions: dict

    def __str__(self):
        return str(self.duts)


# List of available tests to iterate through
AVAILABLE_TESTS = {
    'location': CheckLocation,
    'vendor_id': CheckVendorID,
    'device_id': CheckDeviceID,
    'class_id': CheckClassID,
    'physical_slot': CheckPhysicalSlot,
    'current_link_speed': CheckCurrentLinkSpeed,
    'current_link_width': CheckCurrentLinkWidth,
    'capable_link_speed': CheckCapableLinkSpeed,
    'capable_link_width': CheckCapableLinkWidth,
    'addresses': CheckAddress,
    'check_aer': CheckAER,
}


class OCPOutputObj:
    def __init__(self,
                 devs: ty.Set[PCIDevice],
                 json_path: str,
                 ocp_run: tv.TestRun):
        self._json_path = json_path
        self._devs = devs
        self._ocp_run = ocp_run

    def run(self):
        duts = []
        # Process input json
        with open(self._json_path, 'r') as fh:
            input_json = json.load(fh)
            try:
                duts = input_json['duts']
            except KeyError as _:
                raise json.JSONDecodeError("ERROR: Cannot parse file {}, missing DUTs info.".format(self._json_path))
        for dut in duts:
            for identifier in dut['identifiers']:
                if type(dut['identifiers'][identifier]) != str:
                    dut['identifiers'][identifier] = str(dut['identifiers'][identifier])
        test_sets = []
        for dut in duts:
            identifier = dut['identifiers']
            potential_duts = list(self._devs)
            # Go look for this device and get the data
            missing_devices = []
            if 'address' in identifier:
                if identifier['address'] not in potential_duts:
                    missing_devices.append("No device was found with address {}.".format(identifier['address']))
                potential_duts = [dut for dut in potential_duts if str(dut) == identifier['address']]
            if 'vendor_id' in identifier:
                potential_duts = [dut for dut in potential_duts if dut.vendor_id == identifier['vendor_id']]
                if len(potential_duts) == 0:
                    missing_devices.append("No device was found with vendor id {}.".format(identifier['vendor_id']))
            if 'device_id' in identifier:
                potential_duts = [dut for dut in potential_duts if dut.device_id == identifier['device_id']]
                if len(potential_duts) == 0:
                    missing_devices.append("No device was found with device id {}.".format(identifier['device_id']))
            if len(potential_duts) != 0:
                test_sets.append(TestSet(duts=potential_duts,
                                         conditions=dut['validate']))

        # Create the dut information for the test run
        dut = tv.Dut(id="0", name=socket.gethostname())
        devices_seen = []
        for test_set in test_sets:
            for pcie_device in test_set.duts:
                if pcie_device not in devices_seen:
                    devices_seen.append(pcie_device)
                    dut.add_hardware_info(name=pcie_device.name,
                                          location=str(pcie_device.location),
                                          manufacturer=str(pcie_device.vendor_id),
                                          )
        dut.add_software_info(name="pcicrawler",
                              revision=pkg_resources.get_distribution("pcicrawler").version)

        # Start checks
        if len(missing_devices) > 0:
            diag_passed = False
            for missing_device_msg in missing_devices:
                self._ocp_run.add_log(tv.objects.LogSeverity.ERROR, missing_device_msg)
        with self._ocp_run.scope(dut=dut):
            # First we can list out the missing devices
            for test_set in test_sets:
                # Run checks for each slot
                for dut in test_set.duts:
                    # Run available tests
                    for test_name in AVAILABLE_TESTS:
                        if test_name in test_set.conditions:
                            conditions = test_set.conditions[test_name]
                            if test_name == 'check_aer':
                                conditions = None
                            test = AVAILABLE_TESTS[test_name](
                                dut,
                                conditions,
                                self._ocp_run
                            )
                            result = test.run()
                            if diag_passed and not result:
                                diag_passed = result

            if not diag_passed:
                raise tv.TestRunError(status=tv.objects.TestStatus.COMPLETE,
                                        result=tv.objects.TestResult.FAIL)
