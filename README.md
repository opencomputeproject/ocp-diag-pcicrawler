# pcicrawler
`pcicrawler` is a CLI tool to display/filter/export information about PCI or PCI
Express devices and their topology.

## Usage
`pcicrawler` must be run as root.

The full `--help` message is shown below.
```bash
Usage: pcicrawler [OPTIONS]

  Tool to display/filter/export information about PCI or PCI Express
  devices, as well as their topology.

  Must run as root as it uses privileged sysfs entries.

Options:
  -c, --class-id TEXT             Only show devices matching this PCI class ID
                                  in hex, or one of: nvme, ethernet, raid, gpu
  -d, --device TEXT               Only show devices matching this PCI
                                  vendor/device ID, (syntax like
                                  vendor:device, or vendor:, in hex)
  -e, --express-only / --no-express-only
                                  Only show PCIe devices
  -j, --json / --no-json          Output in JSON format
  -p, --include-path / --no-include-path
                                  Include devices upstream of matched devices
  -s, --addr TEXT                 Show device with this PCI address
  -t, --tree / --no-tree          Output as a tree
  -v, --verbose / --no-verbose    Show debugging output - not compatible with
                                  JSON/tree views
  -V, --vpd / --no-vpd            Include VPD data if present, does not
                                  workwith --tree
  -x, --hexify / --no-hexify      Output vendor/device/class IDs as hex
                                  strings instead of numbers in JSON output
  --help                          Show this message and exit.
```

## Examples
The most common use for `pcicrawler` is calling it with its `--tree` option.

(run as root)
```bash
$ pcicrawler -t
```
```bash
00:00.0 root_port
00:1d.0 root_port, "M.2 PCIE SSD - Boot drive SSD 0", slot 8, device present, speed 8GT/s, width x4
 └─01:00.0 endpoint, Toshiba America Info Systems (1179), device 0116
00:1d.4 root_port, "MEZZ_Conn", slot 12, device present, speed 8GT/s, width x2
 └─02:00.0 endpoint, Mellanox Technologies (15b3) MT27710 Family [ConnectX-4 Lx] (1015)
64:02.0 root_port, "M.2 PCIE SSD - 2nd Storage SSD 2", slot 7, device present, speed 8GT/s, width x4
 └─65:00.0 endpoint, Samsung Electronics Co Ltd (144d), device a808
64:03.0 root_port, "M.2 PCIE SSD - 1st Storage SSD 1", slot 8, device present, speed 8GT/s, width x4
 └─66:00.0 endpoint, Samsung Electronics Co Ltd (144d), device a808
```

Filter the output with the `-s` option.

(run as root)
```bash
$ pcicrawler -s 02:00.0 -t
```
```bash
00:1d.4 root_port, "MEZZ_Conn", slot 12, device present, speed 8GT/s, width x2
 └─02:00.0 endpoint, Mellanox Technologies (15b3) MT27710 Family [ConnectX-4 Lx] (1015)
```

Filter the output, list VPD data (if any), and put into machine-readable format (JSON).

(run as root)
```bash
$ pcicrawler -s 02:00.0 -V -j | python -m json.tool
```
```bash
{
    "0000:02:00.0": {
        "addr": "0000:02:00.0",
        "capable_speed": "8GT/s",
        "capable_width": 2,
        "class_id": 131072,
        "cur_speed": "8GT/s",
        "cur_width": 2,
        "device_id": 4117,
        "express_type": "endpoint",
        "location": "MEZZ_Conn",
        "path": [
            "0000:02:00.0",
            "0000:00:1d.4"
        ],
        "subsystem_device": 633,
        "subsystem_vendor": 5555,
        "target_speed": "8GT/s",
        "vendor_id": 5555,
        "vpd": {
            "fields": {
                "EC": "A2",
                "PN": "MCX4431N-GCAN_FB",
                "SN": "MT1751X14794",
                "V0": "PCIeGen3 x8",
                "V2": "MCX4431N-GCAN_FB",
                "V3": "8427f48749ebe7118000ec0d9ad2c336",
                "VA": "MLX:MODL=CX4431N:MN=MLNX:CSKU=V2:UUID=V3:PCI=V0"
            },
            "identifier_string": "CX4431N - ConnectX-4 LX QSFP28"
        }
    }
}
```

## How `pcicrawler` works
`pcicrawler` retrieves information about a device from its resources in sysfs. For
more information about how devices are organized on the system, visit
https://www.kernel.org/doc/Documentation/filesystems/sysfs-pci.txt.

## Requirements
`pcicrawler` requires Python3 and works with
* CentOS Linux 7

## Building `pcicrawler`
`pcicrawler` is a Python package and a built `.whl` distribution can be made with
```bash
python3 setup.py bdist_wheel
```

## Installing `pcicrawler`
`pcicrawler` is a Python package and can be installed from within the directory with
```bash
python3 setup.py install
```

## Contributing to `pcicrawler`
See the [CONTRIBUTING](CONTRIBUTING.md) file for information on how to help out.

## License
`pcicrawler` is <YOUR LICENSE HERE> licensed, as found in the [LICENSE](LICENSE) file.
