#!/usr/bin/env python3

'''
Copyright (c) Facebook, Inc. and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
'''

from __future__ import absolute_import, division, print_function, unicode_literals

from pci_lib import (
    list_devices,
    map_pci_device,
    maybe_shorten_pci_addr,
    get_dmidecode_pci_slots,
    defer_closes,
    CLASS_ALIASES,
)
from json import dumps

import click
import os
import sys


def jsonify(dev, hexify=False, vpd=False, aer=False):
    jd = dev._asdict()
    exptype = dev.express_type
    explink = dev.express_link
    slot = dev.express_slot
    location = dev.location
    del jd['device_name']
    jd['addr'] = dev.device_name
    if exptype:
        jd['express_type'] = str(exptype)
    if explink:
        jd.update(explink._asdict())
    if slot:
        jd.update(slot._asdict())
    if location:
        jd['location'] = location
    path = [d.device_name for d in dev.get_path()[1:]]
    jd['path'] = path
    if hexify:
        for pad, key in (
                (4, 'vendor_id'),
                (4, 'device_id'),
                (4, 'subsystem_vendor'),
                (4, 'subsystem_device'),
                (6, 'class_id')):
            jd[key] = '{:0{pad}x}'.format(jd[key], pad=pad)
    if vpd:
        if dev.vpd:
            jd['vpd'] = dev.vpd
    if aer:
        aer_info = dev.express_aer
        if aer_info:
            jd['aer'] = aer_info
    return jd


def print_tree_level(devgroups, indent, roots):  # noqa: C901
    spc = indent
    n = 0
    sfx = ' \u2502 '
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
            indent = indent[:-3] + '   '
            spc = spc[:-3] + ' \u2514\u2500'
        elif spc:
            spc = spc[:-3] + ' \u251C\u2500'
        treeline = f'{spc}{click.style(dispaddr, fg="yellow")} '
        if exptype:
            treeline += click.style(str(exptype), fg="red")
        else:
            treeline += click.style('PCI', underline=True)
        if dmidev:
            treeline += ', "{}"'.format(dmidev["designation"])
        if slot:
            treeline += f', slot {click.style(str(slot.slot), fg="blue")}'
            if slot.presence:
                treeline += ', ' + click.style('device present', fg="blue")
            if slot.power is not None:
                power = 'On' if slot.power else 'Off'
                color = 'green' if slot.power else 'red'
                treeline += ', power: ' + click.style(power, fg=color)
            if slot.attn_led != 'unsupported' and slot.attn_led != 'off':
                treeline += ', attn: ' + click.style(slot.attn_led, fg='red')
        if explink:
            if exptype in {'downstream_port', 'root_port'} and \
               explink.cur_width != 0:
                treeline += ', speed ' + \
                            click.style(explink.cur_speed, fg="blue") + \
                            ', width ' + \
                            click.style(f'x{explink.cur_width}', fg="blue")
            # if cur_width == 0 there's no link at all target_speed is None if
            # device doesn't provide it and ports will report < target speed if
            # their downstream endpoint isn't capable of its target speed, so
            # only check endpoints.
            if explink.target_speed and \
               explink.cur_speed != explink.target_speed and \
               exptype == 'endpoint' and \
               explink.cur_width != 0:
                treeline += ', current speed ' + \
                            click.style(explink.cur_speed, fg="blue") + \
                            ' target speed ' + \
                            click.style(explink.target_speed, fg="blue")
        if exptype in {'endpoint',
                       'upstream_port',
                       'root_complex_endpoint'} or exptype is None:
            treeline += ', ' + click.style(dev.name, fg="green")
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
        elif dev.express_type == 'root_port':
            roots.append(dev)
    print_tree_level(devgroups, '', roots)


def no_scripting():
    if not sys.stdout.isatty():
        warning = ('It looks like you may be writing a script that uses pcicrawler. '
                   'Please always use the --json flag from scripts, do NOT parse '
                   'output intended for humans!')
        print(warning)
        print(warning, file=sys.stderr)


@click.command()  # noqa: C901
@click.option(
    '--class-id',
    '-c',
    default=None,
    help='Only show devices matching this PCI class ID in hex, '
    'or one of: ' + ', '.join(CLASS_ALIASES.keys())
)
@click.option(
    '--device',
    '-d',
    default=None,
    help='Only show devices matching this PCI vendor/device ID, '
    '(syntax like vendor:device, or vendor:, in hex)'
)
@click.option(
    '--express-only/--no-express-only',
    '-e',
    default=False,
    help='Only show PCIe devices'
)
@click.option(
    '--json/--no-json', '-j', default=False, help='Output in JSON format'
)
@click.option(
    '--include-path/--no-include-path',
    '-p',
    default=False,
    help='Include devices upstream of matched devices'
)
@click.option(
    '--addr', '-s', default=None, help='Show device with this PCI address'
)
@click.option('--tree/--no-tree', '-t', default=False, help='Output as a tree')
@click.option(
    '--verbose/--no-verbose',
    '-v',
    default=False,
    help='Show debugging output - not compatible with JSON/tree views'
)
@click.option(
    '--vpd/--no-vpd',
    '-V',
    default=False,
    help='Include VPD data if present, does not work with --tree'
)
@click.option(
    "--no-builtin",
    is_flag=True,
    default=False,
    help="Exclude builtin root devices (defaults to true with --tree)"
)
@click.option(
    '--hexify/--no-hexify',
    '-x',
    default=False,
    help='Output vendor/device/class IDs as hex '
    'strings instead of numbers in JSON output'
)
@click.option(
    '--aer/--no-aer',
    '-a',
    default=False,
    help='Include PCIe Advanced Error Reporting (AER) information '
    'when available - only provided in JSON output'
)
def main(
    class_id, device, express_only, json, include_path, addr, tree, verbose,
    vpd, no_builtin, hexify, aer
):
    """
    Tool to display/filter/export information about PCI or PCI Express devices,
    as well as their topology.

    Must run as root as it uses privileged sysfs entries.
    """
    if os.geteuid() != 0:
        print("error: pcicrawler must be run as root.", file=sys.stderr)
        sys.exit(16) # Exit code for root required

    vid = None
    did = None
    devs = []
    if device:
        try:
            vid, did = device.split(':')
            vid = int(vid, 16)
            if did:
                did = int(did, 16)
            else:
                did = None
        except Exception as e:
            raise click.ClickException(
                f'Could not parse vendor/device id: {e}')
    if class_id:
        if class_id in CLASS_ALIASES:
            class_id = CLASS_ALIASES[class_id]
        else:
            class_id = int(class_id, 16)
    if addr:
        dev = map_pci_device(addr)
        if not dev:
            raise click.ClickException(
                f'Could not open PCI dev {addr}')
        devs = [dev]
    else:
        devs = list_devices()

    if vid:
        devs = filter(lambda d: d.vendor_id == vid, devs)
    if did:
        devs = filter(lambda d: d.device_id == did, devs)
    if class_id:
        devs = filter(lambda d: d.class_id == class_id, devs)
    if express_only:
        devs = filter(lambda d: d.express_link is not None, devs)
    if no_builtin:
        devs = filter(lambda d: d.parent or d.express_type == "root_port", devs)

    devs = set(devs)
    if include_path or tree:
        # When asked to print a tree, include filtered devices parents
        for dev in devs.copy():
            devs |= set(dev.get_path())

    if tree:
        no_scripting()
        print_tree(sorted(devs, key=lambda d: d.device_name))
    elif json:
        jdevs = {}
        for dev in devs:
            addr = dev.device_name
            jdevs[addr] = jsonify(dev, hexify=hexify, vpd=vpd, aer=aer)
        click.echo(dumps(jdevs))
    else:
        no_scripting()
        for dev in devs:
            location = dev.location
            exptype = dev.express_type

            line = click.style(str(dev), fg='yellow') + ', '
            if exptype:
                line += f'PCIe {click.style(str(exptype), fg="red")}, '
            line += click.style(dev.name, fg='green')
            click.echo(line)
            if location:
                click.echo(
                    f'  connected via: {click.style(location, bold=True)}')
            if verbose:
                debugging_data = dev.get_debugging_details()
                click.echo(f'  debug: {debugging_data}')
            if vpd:
                if dev.vpd:
                    ident = dev.vpd['identifier_string']
                    if ident:
                        click.echo('  VPD Identifier: '
                                   f'{click.style(ident, bold=True)}')
                    for k, v in dev.vpd['fields'].items():
                        click.echo(f'    {click.style(k, fg="blue")}='
                                   f'{click.style(v, bold=True)}')


if __name__ == "__main__":
    with defer_closes():
        main()
