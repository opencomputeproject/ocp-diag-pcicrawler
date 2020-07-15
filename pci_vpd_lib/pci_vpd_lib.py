'''
Copyright (c) Facebook, Inc. and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
'''

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import io
import os

from collections import OrderedDict


class VPDDataException(Exception):
    pass


class VitalProductDataReader:
    """
    Class for reading Vital Product Data for PCI devices through sysfs.

    It follows the format defined by
    PCI Local Bus Specification v2.2 (Appendix I)
    http://www.ics.uci.edu/~harris/ics216/pci/PCI_22.pdf

    Note: this class avoids touching read/write fields, because this may
          be very slow.
    """

    fields = OrderedDict()
    identifier_string = None

    def __init__(self, vpd_path):
        if os.path.exists(vpd_path):
            with io.FileIO(vpd_path, 'rb') as inf:
                self._read_vpd(inf)

    def _combine_checksum(self, a, buf):
        for x in buf:
            a = (a + x) & 0xFF
        return a

    # Read resource data header, return its tag, size and header checksum.
    def _read_resource_dt_header(self, f):
        checksum = 0
        buf = bytearray(f.read(1))
        # not enough data, return END tag
        if len(buf) < 1:
            return (0xF, 0, 0)
        checksum = self._combine_checksum(checksum, buf)
        # handle small resource data type
        if (buf[0] & 0x80) == 0:
            # small resource data type header just one byte with mask
            # 0b0XXXXYYY, where XXXX is a tag id and YYY is length
            tag = (buf[0] & 0b1111000) >> 3
            length = buf[0] & 0b111
            return (tag, length, checksum)
        else:
            # large resource data type header consists of three bytes
            # first one is 0b1XXXXXXX, where XXXXXXX is a tag id and
            # next two bytes are length with first of the two being least
            # significant
            tag = buf[0] & 0x7F
            # read the length
            buf = bytearray(f.read(2))
            # handle not enough data
            if len(buf) < 2:
                return (0xF, 0, 0)
            checksum = self._combine_checksum(checksum, buf)
            return (tag, buf[0] + buf[1] * 256, checksum)

    # For compatibility between py2.7 and py3, due to different FileIO.read
    # return type.
    def _value_to_str(self, data):
        if type(data) is str:
            return data
        elif type(data) is bytes:
            return data.decode('ascii', errors='replace')

    def _process_vpd_list(self, data):
        # Each item header is 3 bytes long. 2 bytes for the key and 1
        # byte for the length of the data
        while len(data) >= 3:
            key = data[0:2].decode('ascii')
            length = bytearray(data[2:3])[0]

            # Check if we have enough data
            if len(data) < 3 + length:
                return

            # RV is a checksum and reserved bytes. It doesn't provide any
            # useful information and is used just for checksum, skip it.
            if key != 'RV':
                value = self._value_to_str(data[3:3 + length]).strip()
                self.fields[key] = value

            data = data[3 + length:]

    def _read_vpd(self, f):
        tag = 0
        checksum = 0
        while tag != 0xF:
            (tag, l, header_sum) = self._read_resource_dt_header(f)
            if tag == 0x02:
                # 0x02 is an identifier string tag
                value = f.read(l)
                if len(value) < l:
                    raise VPDDataException('VPD data is truncated!')
                checksum =\
                    self._combine_checksum(checksum, bytearray([header_sum]))
                checksum =\
                    self._combine_checksum(checksum, bytearray(value))

                value = self._value_to_str(value).strip()
                self.identifier_string = value
            elif tag == 0x10:
                # This is a VPD-R tag (i.e. read only data), read the data and
                # parse the fields.
                data = f.read(l)
                if len(data) < l:
                    raise VPDDataException('VPD data is truncated!')
                checksum =\
                    self._combine_checksum(checksum, bytearray([header_sum]))
                checksum =\
                    self._combine_checksum(checksum, bytearray(data))

                if checksum != 0:
                    raise VPDDataException('VPD-R checksum failed!')

                self._process_vpd_list(data)
            elif tag == 0x11:
                # This is a VPD-W tag (i.e. read/write data, skip it).
                f.seek(l, io.SEEK_CUR)
            elif tag != 0xF:
                raise VPDDataException('Unknown VPD tag {}!'.format(tag))
