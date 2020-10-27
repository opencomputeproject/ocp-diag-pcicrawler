'''
Copyright (c) Facebook, Inc. and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
'''

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from collections import defaultdict, namedtuple
from os.path import realpath
from contextlib import contextmanager, closing
from pci_vpd_lib.pci_vpd_lib import (
    VitalProductDataReader,
    VPDDataException,
)
from six import int2byte, iterbytes
import re
import os
import logging
import struct
import subprocess

log = logging.getLogger(__name__)


class cached_property(object):
    """
    A property that is only computed once per instance and then replaces itself
    with an ordinary attribute. Deleting the attribute resets the property.
    """
    def __init__(self, func):
        self.func = func
        self.__doc__ = func.__doc__

    def __get__(self, obj, cls):
        if obj is None:
            return self
        value = obj.__dict__[self.func.__name__] = self.func(obj)
        return value

CLASS_ALIASES = {  # noqa
    'nvme': 0x010802,
    'ethernet': 0x020000,
    'raid': 0x010400,
    'gpu': 0x030200,
}
PCI_CLASS_MASKS = {
    'nvme': (0x010802, 0xFFFFFF),
    'ethernet': (0x020000, 0xFFFF00),
    'raid': (0x010400, 0xFFFF00),
    'gpu': (0x030000, 0xFF0000),
}
SYSFS_PCI_BUS_DEVICES = "/sys/bus/pci/devices/"

PCI_HAS_CAP_LIST = 0x10
PCI_CONFIG_VENDOR_OFFSET = 0x4
PCI_CAP_LIST_PTR = 0x34
PCI_CAP_FLAGS = 0x2
PCI_CAP_EXPRESS = 0x10
PCI_EXP_LNKCAP = 0xc
PCI_EXP_LNKSTA = 0x12
PCI_EXP_LNKCTL2 = 0x30
PCI_EXP_SLTCAP = 0x14
PCI_EXP_SLTCTL = 0x18
PCI_EXP_SLTSTA = 0x1a
PCI_EXP_SLOT_CAP_POWER = 1 << 1
PCI_EXP_SLOT_CAP_ATTN_LED = 1 << 3
PCI_EXP_SLOT_CTL_POWER = 1 << 10
PCI_EXP_SLOT_PRESENCE = 1 << 6
PCI_EXP_SLOT_CTL_ATTN_LED_MASK = 0x00c0
PCI_EXPRESS_FLAG_SLOT = 0x0100
PCI_STATUS_REGISTER = 0x6

PCI_EXP_SLOT_CTL_ATTN_LED_VALUES = {
    0x00: 'reserved',
    0x00c0: 'off',
    0x0080: 'blink',
    0x0040: 'on',
}


# Long PCI address format is as follows
# Domain(32bits):Bus(8bits):Device(5bits):Function(3bits)
# Domain is *not* always 0! (ARM systems have multiple ones)
LONG_PCI_ADDR_REGEX = re.compile(
    r'^([0-9a-fA-F]{2,8}):([0-9a-fA-F]{2}):([01][0-9a-fA-F])[:\.]0*([0-7])$')

# Short PCI address format is as follows
# Bus(8bits):Device(5bits).Function(3bits)
SHORT_PCI_ADDR_REGEX = re.compile(r'^([0-9a-fA-F]{2}):([01][0-9a-fA-F])\.([0-7])$')


class NonZeroDomain(Exception):
    """
    Cannot shorten PCI addrs with a non-zero Domain
    """
    pass


class CapabilityDecodeError(Exception):
    """
    Could not decode PCI capabilities from PCI config space
    """
    pass


def read_u32(data, offset):
    r, = struct.unpack('<L', data[offset:offset + 4])
    return r


def read_u16(data, offset):
    r, = struct.unpack('<H', data[offset:offset + 2])
    return r


def read_u8(data, offset):
    r, = struct.unpack('B', data[offset:offset + 1])
    return r


def find_capability(config, cap):
    """
    Finds the offset of a particular capability register in a PCI configuration
    space
    """
    vendor = read_u16(config, PCI_CONFIG_VENDOR_OFFSET)
    if vendor == 0xffff:
        # This indicates the device has likely gone missing
        config.exceptions.add(
            CapabilityDecodeError('PCI config space for device is inaccessible')
        )
        return None
    status = read_u16(config, PCI_STATUS_REGISTER)
    if (status & PCI_HAS_CAP_LIST) == 0:
        return None

    # detect looping
    config.been_there = defaultdict(bool)
    pos = PCI_CAP_LIST_PTR
    while pos < len(config):
        if config.been_there[pos]:
            config.exceptions.add(
                CapabilityDecodeError('Detected looping in capability decoding')
            )
            return None
        config.been_there[pos] = True
        pos = read_u8(config, pos)
        cap_id = read_u8(config, pos)
        if cap_id == cap:
            return pos
        pos += 1
    # we exhausted the config space without finding it


@contextmanager
def raw_open(path, flags):
    fd = os.open(path, flags)
    yield fd
    os.close(fd)


EXPRESS_TYPES = {
    0x0: 'endpoint',
    0x1: 'legacy_endpoint',
    0x4: 'root_port',
    0x5: 'upstream_port',
    0x6: 'downstream_port',
    0x7: 'pci_bridge',
    0x8: 'pcie_bridge',
    0x9: 'root_complex_endpoint',
    0xa: 'root_complex_event_collector',
}

EXPRESS_SPEED = {
    1: "2.5GT/s",
    2: "5GT/s",
    3: "8GT/s",
    4: "16GT/s",
}


PCIExpressLink = namedtuple('PCIExpressLink',
                            ('cur_speed', 'cur_width',
                             'capable_speed', 'capable_width',
                             'target_speed'))

PCIExpressSlot = namedtuple('PCIExpressSlot',
                            ('slot', 'presence', 'power', 'attn_led'))


def align32(start, stop):
    # Mask off low bits of start to align down
    astart = start & ~0b11
    # If stop is aligned leave as is
    if stop & 0b11 == 0:
        astop = stop
    else:
        # Otherwise align-down and add 4
        astop = (stop & ~0b11) + 4
    return astart, astop


class PCIConfigSpace(object):
    """Caching view of a PCI(e) device's config space

    Index with slices to get bytes

    Usage:

    config = PCIConfigSpace.get(address)
    bytes = config[10:15]
    """

    configspaces = {}
    deferclose = False
    defer = set()

    def __init__(self, devname):
        if devname in PCIConfigSpace.configspaces:
            raise Exception(
                'Attempt to open PCI config space twice for device: {}'.
                format(self)
            )
        self.device_name = devname
        self.path = '{}{}/config'.format(
            SYSFS_PCI_BUS_DEVICES, self.device_name
        )
        self.fd = None
        self.open()
        self.size = self._stat_size()
        self.cache = [None] * self.size
        self.been_there = defaultdict(bool)
        self.exceptions = set()
        PCIConfigSpace.configspaces[devname] = self

    @classmethod
    def begin_defer(cls):
        cls.deferclose = True

    @classmethod
    def end_defer(cls):
        cls.deferclose = False
        for inst in cls.defer:
            inst.close()
        cls.defer = set()

    def _stat_size(self):
        return os.fstat(self.fd).st_size

    # soft errors which are of note but do not end execution
    def encountered_errors(self):
        return self.exceptions

    # use after closing to determine entire affected PCI config space
    # use during to determing space used for certain operations
    def used_config_space(self):
        return list(self.been_there.keys())

    def open(self):
        if self.fd is None:
            self.fd = os.open(self.path, os.O_RDONLY)

    def close(self):
        if PCIConfigSpace.deferclose:
            PCIConfigSpace.defer.add(self)
            return
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __getitem__(self, k):
        if isinstance(k, slice):
            if k.step is not None:
                raise Exception('Cannot step in PCI config space')
            start = k.start
            stop = k.stop
        else:
            raise Exception('Can only use slices to index config space')
        if stop > self.size:
            raise Exception(
                'Attempt to read at {}, past end of config space: {}'.format(
                    (start, stop), self
                )
            )
        data = self.cache[start:stop]
        if any(d is None for d in data):
            self.open()
            # Align to 32 bit words
            rstart, rstop = align32(start, stop)
            os.lseek(self.fd, rstart, os.SEEK_SET)
            data = os.read(self.fd, rstop - rstart)
            self.cache[rstart:rstop] = list(iterbytes(data))
            data = self.cache[start:stop]
        return b''.join(int2byte(b) for b in data)

    def __len__(self):
        return self.size

    @classmethod
    def get(cls, devname):
        if devname in cls.configspaces:
            return cls.configspaces[devname]
        return PCIConfigSpace(devname)

    def __repr__(self):
        return 'PCIConfigSpace(\'{}\', sz={})'.format(
            self.device_name, self.size
        )


@contextmanager
def defer_closes():
    """
    Use with `with` to make pci_lib reuse fds and defer closing them until the
    end of your with block.

    Usage:

    with pci_lib.defer_closes():
        [... do some stuff with PCIDevice objects ...]
    """
    try:
        PCIConfigSpace.begin_defer()
        yield
    finally:
        PCIConfigSpace.end_defer()


class PCIDevice(namedtuple('PCIDevice', ('device_name', 'vendor_id',
                                         'device_id', 'class_id',
                                         'subsystem_vendor',
                                         'subsystem_device'))):

    @property
    def domain_id(self):
        return int(self.device_name.split(":", 1)[0], 16)

    @property
    def bus_id(self):
        return int(self.device_name.split(":", 2)[1], 16)

    @property
    def bus_device_id(self):
        return int(self.device_name.rsplit(":", 1)[1].split(".", 1)[0], 16)

    @property
    def device_function_id(self):
        return int(self.device_name.rsplit(".", 1)[1], 16)

    @cached_property
    def parent(self):
        """
        Get this device's parent device.

        @return a PCIDevice, or None if this device is a top-level device
        """
        mypath = os.path.join(SYSFS_PCI_BUS_DEVICES, self.device_name)
        mypath = realpath(mypath)
        parentpath = realpath(os.path.join(mypath, '..'))
        parentdev = os.path.basename(parentpath)
        if not re.match(LONG_PCI_ADDR_REGEX, parentdev):
            return None
        return map_pci_device(parentdev)

    @cached_property
    def name(self):
        """
        Human readable device name, if available. Uses
        /usr/share/hwdata/pci.ids to map vendor and device ID to names.
        """
        vendor, device = lookup_device(self.vendor_id, self.device_id)
        if vendor and device:
            return '{} ({:04x}) {} ({:04x})'.format(
                vendor, self.vendor_id, device, self.device_id)
        if vendor:
            return '{} ({:04x}), device {:04x}'.format(
                vendor, self.vendor_id, self.device_id)
        return '{:04x}:{:04x}'.format(self.vendor_id, self.device_id)

    @cached_property
    def location(self):
        """
        A string describing how this device is connected to the system

        None if this is a built-in device
        """
        path = self.get_path()
        slotmap = get_dmidecode_pci_slots()
        connection = path[1:]
        if connection:
            pathels = []
            for dev in connection:
                pathpart = []
                dmislot = slotmap.get(dev.device_name)
                if dmislot:
                    pathels.append(dmislot['designation'])
                    continue
                explink = dev.express_link
                exptype = dev.express_type
                slot = dev.express_slot
                if slot:
                    pathpart.append('slot {}'.format(slot.slot))
                if explink:
                    pathpart.append('{}'.format(exptype))
                if pathpart:
                    pathels.append(', '.join(pathpart))
            return ' -> '.join(pathels)
        else:
            return None

    @cached_property
    def vpd(self):
        vpdfile = '{}{}/vpd'.format(SYSFS_PCI_BUS_DEVICES, self.device_name)
        if os.path.exists(vpdfile):
            try:
                vpdreader = VitalProductDataReader(vpdfile)
                return {'identifier_string': vpdreader.identifier_string,
                        'fields': vpdreader.fields}
            except VPDDataException:
                return None
            except OSError:
                # VPD was unreadable, rather than readable but unparseable.
                return None
        return None

    def get_path(self):
        """
        Get a list of PCIDevices including this device and all devices up to
        the root port it is connected to.
        """
        path = []
        current = self
        while current:
            path.append(current)
            current = current.parent
        return path

    def get_debugging_details(self):
        """
        Get a set of errors we encountered while trying to request information
        about the device until this point, as well as the used config space.
        """
        with closing(PCIConfigSpace.get(self.device_name)) as config:
            return (config.encountered_errors(), config.used_config_space())

    @cached_property
    def express_cap_version(self):
        """
        Version number of the Express capability register.

        None if not a PCIe device.
        """
        with closing(PCIConfigSpace.get(self.device_name)) as config:
            express = find_capability(config, PCI_CAP_EXPRESS)
            if express is None:
                return None
            flags = read_u16(config, express + PCI_CAP_FLAGS)
            version = flags & 0xf
            return version

    @cached_property
    def express_slot(self):
        """
        Retrieve PCI-Express slot information.

        Returns None if this is not a PCIe slot, otherwise returns a
        PCIExpressSlot

        The 'power' field will be a bool indicating this slot's current power
        setting if it has power control capability, otherwise the 'power' field
        will be set to None.
        """
        with closing(PCIConfigSpace.get(self.device_name)) as config:
            express = find_capability(config, PCI_CAP_EXPRESS)
            if express is None:
                return None
            flags = read_u16(config, express + PCI_CAP_FLAGS)
            if (flags & PCI_EXPRESS_FLAG_SLOT) == 0:
                return None
            slotcap = read_u32(config, express + PCI_EXP_SLTCAP)
            slotstatus = read_u32(config, express + PCI_EXP_SLTSTA)
            slotctl = read_u32(config, express + PCI_EXP_SLTCTL)

            slotnum = slotcap >> 19
            presence = (slotstatus & PCI_EXP_SLOT_PRESENCE) != 0

            if slotcap & PCI_EXP_SLOT_CAP_ATTN_LED != 0:
                attn_led = PCI_EXP_SLOT_CTL_ATTN_LED_VALUES.get(
                    slotctl & PCI_EXP_SLOT_CTL_ATTN_LED_MASK, 'off')
            else:
                attn_led = 'unsupported'

            power = None
            if (slotcap & PCI_EXP_SLOT_CAP_POWER) != 0:
                # 1 is powered -off-, 0 is powered -on-
                power = (slotctl & PCI_EXP_SLOT_CTL_POWER) == 0
            return PCIExpressSlot(slotnum, presence, power, attn_led)

    @cached_property
    def express_aer(self):
        """
        Retrieve the device's PCIe Advanced Error Reporting (AER) statistics,
        if the device is AER capable and the kernel provides the corresponding
        pseudo fs interface under /sys/bus/pci/devices/.  The information is
        gleaned from the following files, when present:

        For devices:
        /sys/bus/pci/devices/<dev>/aer_dev_correctable
        /sys/bus/pci/devices/<dev>/aer_dev_fatal
        /sys/bus/pci/devices/<dev>/aer_dev_nonfatal

        along with the following for Root Port error counts:
        /sys/bus/pci/devices/<dev>/aer_rootport_total_err_cor
        /sys/bus/pci/devices/<dev>/aer_rootport_total_err_fatal
        /sys/bus/pci/devices/<dev>/aer_rootport_total_err_nonfatal

        Returns a dictionary with device and rootport dictionaries containing
        various key/value pairs or counts provided via the pseudo fs files.
        Empty device/rootport dictionaries are not included and None is
        returned when no AER information has been found.
        """
        if self.express_type is None:
            return None
        aer = {}
        dev_stats = aer_dev_stats(self.device_name, ['aer_dev_correctable',
                                                     'aer_dev_fatal',
                                                     'aer_dev_nonfatal'])
        if dev_stats is not None:
            aer["device"] = dev_stats

        if self.express_type == 'root_port':
            rp_counts = aer_rootport_counts(self.device_name,
                                            ['aer_rootport_total_err_cor',
                                             'aer_rootport_total_err_fatal',
                                             'aer_rootport_total_err_nonfatal'])
            if rp_counts is not None:
                aer["rootport"] = rp_counts
        if len(aer) == 0:
            return None
        return aer

    @cached_property
    def express_type(self):
        """
        @return PCI-Express device type, None if this is a PCI device.
        """
        with closing(PCIConfigSpace.get(self.device_name)) as config:
            express = find_capability(config, PCI_CAP_EXPRESS)
            if express is None:
                return None
            flags = read_u16(config, express + PCI_CAP_FLAGS)
            exptype = EXPRESS_TYPES.get((flags & 0xf0) >> 4)
            return exptype

    @cached_property
    def express_link(self):
        """
        Retrieve the PCI-Express link status.

        Returns None if this is not a PCIe device, otherwise returns a
        PCIExpressLink
        """

        with closing(PCIConfigSpace.get(self.device_name)) as config:
            express = find_capability(config, PCI_CAP_EXPRESS)
            if express is None:
                return None
            flags = read_u16(config, express + PCI_CAP_FLAGS)
            exptype = EXPRESS_TYPES.get((flags & 0xf0) >> 4)
            if exptype is None:
                return None
            if exptype not in ['root_complex_endpoint',
                               'root_complex_event_collector']:
                lnksta = read_u16(config, express + PCI_EXP_LNKSTA)
                lnkcap = read_u16(config, express + PCI_EXP_LNKCAP)
                lnkctl2 = None
                if self.express_cap_version >= 2:
                    lnkctl2 = read_u16(config, express + PCI_EXP_LNKCTL2)

                cur_speed = EXPRESS_SPEED.get(lnksta & 0xf, 'unknown')
                cur_width = ((lnksta & 0x3f0) >> 4)
                capable_speed = EXPRESS_SPEED.get(lnkcap & 0xf, 'unknown')
                capable_width = ((lnkcap & 0x3f0) >> 4)
                target_speed = None
                if lnkctl2 is not None:
                    target_speed = EXPRESS_SPEED.get(lnkctl2 & 0xf, None)

                return PCIExpressLink(cur_speed, cur_width,
                                      capable_speed, capable_width, target_speed)

    def __repr__(self):
        return ('<PCIDevice vendor_id=0x{p.vendor_id:04X} '
                'device_id=0x{p.device_id:04X} class_id=0x{p.class_id:04x} '
                'domain_id=0x{p.domain_id:04X} bus_id=0x{p.bus_id:04X} '
                'bus_device_id=0x{p.bus_device_id:04X} '
                'device_function_id=0x{p.device_function_id:04X}>').format(
                    p=self)

    def __str__(self):
        return self.device_name


def get_dmidecode_pci_slots():
    slotmap = {}
    try:
        dmiout = subprocess.check_output(['/sbin/dmidecode', '-t', 'slot'])
        dmiout = dmiout.decode('utf-8')
        slots = []
        slot = None
        for line in dmiout.splitlines():
            line = line.strip()
            if line == 'System Slot Information':
                slot = {}
            if ': ' in line:
                k, v = line.split(': ', 1)
                slot[k] = v
            if slot and not line:
                slots.append(slot)
                slot = {}
        if slot and not line:
            slots.append(slot)
        for slot in slots:
            try:
                addr = slot['Bus Address'].lower()
                slotmap[addr] = {
                    'designation': slot['Designation'],
                    'type': slot['Type'],
                }
            except KeyError:
                # skip
                pass
    except Exception:
        pass
    return slotmap


def aer_dev_stats(device_name, stat_names):
    """Gather PCIe device AER information, when available."""
    dev_stats = {}
    device_name = expand_pci_addr(device_name)
    if not device_name:
        return None
    for stat_name in stat_names:
        filename = os.path.join(SYSFS_PCI_BUS_DEVICES, device_name, stat_name)
        if not os.path.isfile(filename):
            continue
        with open(filename) as file_obj:
            # For AER device stats we expect multiple lines, each with a key
            # and value. For example:
            #   RxErr 0
            #   BadTLP 0
            #   BadDLLP 0
            stats = {}
            for line in file_obj.readlines():
                key, value = line.strip().split()
                try:
                    stats[key] = int(value)
                except ValueError:
                    pass
            dev_stats[stat_name] = stats

    if len(dev_stats) == 0:
        return None
    return dev_stats


def aer_rootport_counts(device_name, count_names):
    """Gather PCIe root port device AER error counts, when available."""
    rootport_counts = {}
    device_name = expand_pci_addr(device_name)
    if not device_name:
        return None
    for count_name in count_names:
        filename = os.path.join(SYSFS_PCI_BUS_DEVICES, device_name, count_name)
        if not os.path.isfile(filename):
            continue
        with open(filename) as file_obj:
            # For AER root port counts we expect a single line with
            # the integer error count that is associated with the
            # specific count_name.  We'll use the count_name for the
            # value's key.
            try:
                rootport_counts[count_name] = int(file_obj.readline())
            except ValueError:
                pass

    if len(rootport_counts) == 0:
        return None

    return rootport_counts


def map_pci_device(device_name):
    device_name = expand_pci_addr(device_name)
    if not device_name:
        return None
    dev_path = os.path.join(SYSFS_PCI_BUS_DEVICES, device_name)
    with open(os.path.join(dev_path, "vendor")) as vendor_fd:
        vendor = int(vendor_fd.read(), 16)
    with open(os.path.join(dev_path, "device")) as device_fd:
        device = int(device_fd.read(), 16)
    with open(os.path.join(dev_path, "class")) as pci_class_fd:
        pci_class = int(pci_class_fd.read(), 16)
    with open(os.path.join(dev_path, "subsystem_vendor")) as pci_sv_fd:
        pci_subvendor = int(pci_sv_fd.read(), 16)
    with open(os.path.join(dev_path, "subsystem_device")) as pci_ss_fd:
        pci_subsystem = int(pci_ss_fd.read(), 16)
    return PCIDevice(device_name, vendor, device, pci_class,
                     pci_subvendor, pci_subsystem)


def list_devices():
    for device in os.listdir(SYSFS_PCI_BUS_DEVICES):
        yield map_pci_device(device)


def find_devices(**kwargs):
    # XXX: check kwargs against PCIDevice
    for pci_device in list_devices():
        for key, val in kwargs.items():
            v = getattr(pci_device, key)
            if isinstance(val, (list, tuple, set)):
                if v not in val:
                    break
            else:
                if v != val:
                    break
        else:
            # all specified keys match
            yield pci_device


def expand_pci_addr(pci_addr):
    '''
    Convert a possibly shortened PCI address to its expanded form, including
    normalizing the formatting of long addresses
    '''

    m1 = LONG_PCI_ADDR_REGEX.match(pci_addr)
    m2 = SHORT_PCI_ADDR_REGEX.match(pci_addr)

    if m1:
        domain, bus, device, function = \
            map(lambda n: int(n, 16), m1.groups())
        return '{:04x}:{:02x}:{:02x}.{:x}'.format(
            domain, bus, device, function)
    if m2:
        bus, device, function = \
            map(lambda n: int(n, 16), m2.groups())
        return '{:04x}:{:02x}:{:02x}.{:x}'.format(
            0, bus, device, function)
    return None


def maybe_shorten_pci_addr(pci_addr):
    '''Shorten a PCI address, but only if its domain is not 0'''
    try:
        return shorten_pci_addr(pci_addr)
    except NonZeroDomain:
        return pci_addr


def shorten_pci_addr(pci_addr):
    '''
    Convert a long pci address to the short version, nothing to be done if pci
    address is already a short version.

    Short addresses do not necessarily uniquely identify a device! Only use
    this for displaying address to humans. This will raise NonZeroBus if passed
    an address that cannot be shortened. Consider `maybe_shorten_pci_addr`
    '''

    m1 = LONG_PCI_ADDR_REGEX.match(pci_addr)
    m2 = SHORT_PCI_ADDR_REGEX.match(pci_addr)
    if m1:
        if m1.group(1) != '0000':
            raise NonZeroDomain()
        pci_addr = '{}:{}.{}'.format(
            m1.group(2), m1.group(3), m1.group(4))
    elif m2:
        pass
    else:
        log.error('Invalid pci address %s', pci_addr)
        pci_addr = None

    return pci_addr


def load_pci_ids():
    db = {}
    pci_ids_locations = [
        "/usr/share/hwdata/pci.ids",
        "/usr/share/misc/pci.ids",
    ]
    pci_ids_location = None
    for loc in pci_ids_locations:
        if os.path.isfile(loc):
            pci_ids_location = loc
            break
    if not pci_ids_location:
        raise RuntimeError(
            "No pci.ids file avail in %r" % pci_ids_locations
        )

    with open(pci_ids_location) as f:
        vid = None
        did = None
        for line in f:
            if line.startswith('#'):
                continue
            if line.startswith('C '):
                vid = None
                continue
            if len(line.strip()) == 0:
                continue
            parts = line.split('  ', 1)
            if parts[0].startswith("\t\t"):
                if not (vid and did):
                    continue
                subvid, subdid = parts[0].split()
                subvid = int(subvid, 16)
                subdid = int(subdid, 16)
                name = parts[1].strip()
                db[(vid, did, subvid, subdid)] = name
                continue
            if parts[0].startswith("\t"):
                if not vid:
                    continue
                did = parts[0].strip()
                did = int(did, 16)
                name = parts[1].strip()
                db[(vid, did)] = name
                continue
            vid = parts[0]
            vid = int(vid, 16)
            name = parts[1].strip()
            db[vid] = name
            did = None
    return db


pci_db = None
no_pci_db = False


def get_pci_db():
    global pci_db
    global no_pci_db
    if no_pci_db:
        return None
    if pci_db:
        return pci_db
    else:
        pci_db = load_pci_ids()
        if pci_db is None:
            no_pci_db = True
        return pci_db


def lookup_device(vendorid, deviceid):
    db = get_pci_db()
    if db is None:
        return None, None
    vendor = db.get(vendorid)
    device = db.get((vendorid, deviceid))
    return vendor, device
