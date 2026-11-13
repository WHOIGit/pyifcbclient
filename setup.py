#!/usr/bin/env python
from setuptools import setup


setup(
    name="pyifcbclient",
    version="1.0",
    description="Interface to the McLane Imaging FlowCytobot websocket API",
    author="Ryan Govostes",
    author_email="rgovostes@whoi.edu",
    url="https://github.com/WHOIGit/pyifcbclient",
    packages=["ifcbclient"],
    python_requires=">=3.8",

    install_requires=["websockets>=13.1,<15"],
)
