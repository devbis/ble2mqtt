#!/usr/bin/env python

from setuptools import find_packages, setup

from ble2mqtt.__version__ import VERSION

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name='ble2mqtt',
    version=VERSION,
    description='BLE to MQTT bridge',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author='Ivan Belokobylskiy',
    author_email='belokobylskij@gmail.com',
    url='https://github.com/devbis/ble2mqtt/',
    entry_points={
        'console_scripts': ['ble2mqtt=ble2mqtt.__main__:main']
    },
    packages=find_packages(include=['ble2mqtt', 'ble2mqtt.*']),
    install_requires=[
        'aio-mqtt>=0.2.0',
        'bleak>=0.12.0',
    ],
    dependency_links=['http://github.com/hbldh/bleak/tarball/dbus-next-2#egg=bleak-0.12.0'],
    classifiers=[
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3 :: Only',
        'Topic :: Utilities',
    ],
)
