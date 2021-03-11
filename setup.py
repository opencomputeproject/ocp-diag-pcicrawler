#!/usr/bin/env python3
# -*- coding: utf-8 -*-

'''
Copyright (c) Facebook, Inc. and its affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
'''

from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os

from setuptools import setup, find_packages

# Package meta-data.
NAME = 'pcicrawler'
DESCRIPTION = ('Open source tool to display/filter/export information about PCI'
    ' or PCI Express devices, as well as their topology.')
URL = 'https://github.com/facebook/pcicrawler'
EMAIL = 'ashwinp@fb.com'
AUTHOR = 'Ashwin Poojary'
REQUIRES_PYTHON = '>=3.6.0'
VERSION = "1.0.0" 

# What packages are required for this module to be executed?
REQUIRED = [
    'click',
    'six',
]

# What packages are optional?
EXTRAS = {
    # 'fancy feature': ['django'],
}

here = os.path.abspath(os.path.dirname(__file__))

# Import the README and use it as the long-description.
# Note: this will only work if 'README.md' is present in your MANIFEST.in file!
try:
    with io.open(os.path.join(here, 'README.md'), encoding='utf-8') as f:
        long_description = '\n' + f.read()
except FileNotFoundError:
    long_description = DESCRIPTION

# Load the package's __version__.py module as a dictionary.
about = {}
if not VERSION:
    project_slug = NAME.lower().replace("-", "_").replace(" ", "_")
    with open(os.path.join(here, project_slug, '__version__.py')) as f:
        exec(f.read(), about)
else:
    about['__version__'] = VERSION

# setup
setup(
    name=NAME,
    version=about['__version__'],
    description=DESCRIPTION,
    long_description=long_description,
    long_description_content_type='text/markdown',
    author=AUTHOR,
    author_email=EMAIL,
    python_requires=REQUIRES_PYTHON,
    url=URL,
    packages=find_packages(exclude=('tests',)),
    entry_points={
        'console_scripts': ['pcicrawler=pcicrawler.cli:main'],
    },
    install_requires=REQUIRED,
    extras_require=EXTRAS,
    include_package_data=True,
    license='MIT',
)
