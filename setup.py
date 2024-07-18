"""Install script."""

from setuptools import find_packages, setup

VERSION = "0.1-alpha"  # single source of truth


with open("./voy/version.py", "w") as f:
    f.write("__version__ = '{}'\n".format(VERSION))


setup(
    name="voy",
    version=VERSION,
    description="A CLI for following arxiv authors.",
    entry_points={
        "console_scripts": [
            "voy=voy.cmd:voy",
        ]
    },
    packages=find_packages(),
    url="https://github.com/floringogianu/voy",
    author="Florin Gogianu",
    author_email="florin.gogianu@gmail.com",
    license="MIT",
    install_requires=[
        "arxiv~=2.1.0",
        "colorful~=0.5.6",
        "datargs~=1.1.0",
        "jsonlines~=4.0.0",
        "platformdirs~=4.1.0",
        "xxhash~=3.4.1",
        "windows-curses~=2.3.3; sys_platform == 'win32'"
    ],
    
    zip_safe=False,
)
