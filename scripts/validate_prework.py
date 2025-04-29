# Copyright (C) 2025 Infineon Technologies
# SPDX-License-Identifier: Apache-2.0

import sys
import os
import importlib
import subprocess
import platform
import re
import socket
import struct

from west.commands import WestCommand, Verbosity, CommandError
from west import log, manifest


class ValidatePrework(WestCommand):

    def __init__(self):
        self._modules = {}
        self._error_count = 0

        super().__init__(
            "validate-prework",
            "validate environment after prework",
            "Validate environment after prework by running this command without parameters.",
        )

    def do_add_parser(self, parser_adder):
        return parser_adder.add_parser(
            self.name, help=self.help, description=self.description
        )

    def do_run(self, args, unknown_args):
        try:
            self._import_modules(["zcmake"])
        except (ImportError, RuntimeError, FileNotFoundError) as e:
            self.err(str(e))
            if self.verbosity >= Verbosity.DBG_MORE:
                raise e
            else:
                raise CommandError(1) from e

        self._do_validate()

        if self._error_count == 0:
            self.inf("Validation succeeded!", colorize=True)
        else:
            raise CommandError(1)

    def _import_modules(self, names):
        # Get ZEPHYR_BASE environment variable and confirm existence
        ZEPHYR_BASE = os.getenv("ZEPHYR_BASE")
        if not ZEPHYR_BASE:
            raise RuntimeError("Environment variable ZEPHYR_BASE not set")

        self.dbg("  -- ZEPHYR_BASE: " + ZEPHYR_BASE)

        # Confirm existence of directory scripts/west_commands
        west_commands_dir = ZEPHYR_BASE + "/scripts/west_commands"
        if not os.path.exists(west_commands_dir):
            raise FileNotFoundError(
                "Can't find Zephyr west_commands directory at: " + west_commands_dir
            )

        # Load modules from scripts/west_commands
        sys.path.insert(0, west_commands_dir)
        for name in names:
            try:
                self._modules[name] = importlib.import_module(name)
            except ImportError as e:
                raise ImportError(
                    "Failed to import Python module '" + name + "'"
                ) from e

    def err(self, *args, fatal: bool = False, end: str = "\n"):
        self._error_count = self._error_count + 1
        super().err(*args, fatal=fatal, end=end)

    def _do_validate(self):

        # Verify that CYW20829 BT FW blobs have been fetched
        try:
            # Get hal_infineon project from manifest
            hal_infineon_project = self.manifest.get_projects(["hal_infineon"])[0]

            bt_fw_path = (
                hal_infineon_project.abspath
                + "/zephyr/blobs/img/bluetooth/firmware/COMPONENT_CYW20829B0"
            )
            self.dbg("  -- Bluetooth FW path: " + bt_fw_path)

            if not os.path.exists(bt_fw_path):
                self.err(
                    "CYW20829 Bluetooth Firmware binary folder '"
                    + bt_fw_path
                    + "' not found\n\t"
                    "Did you forget to do 'west blobs fetch hal_infineon' first?\n\t"
                    "See: https://docs.zephyrproject.org/latest/boards/infineon/cyw920829m2evk_02/doc/index.html#fetch-binary-blobs"
                )
        except ValueError:
            self.err(
                "Can't find 'hal_infineon' project in manifest:", self.manifest.abspath
            )

        # Check for permanent CMake argument to set OPENOCD variable
        build_cmake_args = self.config.get("build.cmake-args")
        if build_cmake_args:
            self.dbg("  -- build.cmake-args: " + build_cmake_args)

        if not build_cmake_args or "OPENOCD" not in build_cmake_args:
            self.wrn(
                "No permanent CMake argument configured to set CMake variable OPENOCD\n\t"
                "Did you forget to do 'west config ...'?\n\t"
                "See: https://docs.zephyrproject.org/latest/boards/infineon/cyw920829m2evk_02/doc/index.html#west-commands"
            )

        # Check for presence of build directory
        build_dir = "./build"
        if not os.path.exists(build_dir):
            self.err(
                "Can't find build directory at: '" + build_dir + "'\n\t"
                "Did you forget to do 'west build ...' first?\n\t"
                "See: https://docs.zephyrproject.org/latest/develop/getting_started/index.html#build-the-blinky-sample"
            )
        else:
            # Try to read CMakeCache.txt
            try:
                build_dir = "build"
                cache_file = os.path.join(
                    build_dir, self._modules["zcmake"].DEFAULT_CACHE
                )
                self.dbg("  -- CMake cache: " + cache_file)

                cache = self._modules["zcmake"].CMakeCache(cache_file)

                # Warn if missing CMAKE_EXPORT_COMPILE_COMMANDS
                if not cache.get("CMAKE_EXPORT_COMPILE_COMMANDS"):
                    self.wrn(
                        "CMAKE_EXPORT_COMPILE_COMMANDS is not set, VS Code intellisense may suffer"
                    )

                # Verify that Infineon custom OpenOCD (with adapter 'kitprog3' support) is being used
                openocd = cache.get("OPENOCD")
                if not openocd:
                    self.wrn("OPENOCD not configured in CMake cache")
                    openocd = "openocd"

                try:
                    # Execute OpenOCD and select adapter driver 'kitprog3' to verify adapter driver existence
                    result = subprocess.run(
                        [openocd, "-c", "adapter driver kitprog3;shutdown"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    if result.returncode != 0:
                        self.err(
                            "OpenOCD '"
                            + openocd
                            + "' lacks support for Infineon adapter 'kitprog3'\n\t"
                            + "See: https://docs.zephyrproject.org/latest/boards/infineon/cyw920829m2evk_02/doc/index.html#infineon-openocd-installation"
                        )

                except FileNotFoundError:
                    self.err(
                        "Couldn't find OpenOCD executable '" + openocd + "'\n\t"
                        "(tip: try using single quotes for paths containing spaces when specifying the CMake variable OPENOCD)"
                    )
                except PermissionError:
                    self.err(
                        "Permission error when trying to execute OpenOCD executable '"
                        + openocd
                        + "'"
                    )

            except FileNotFoundError as e:
                self.err(
                    "Can't find CMakeCache.txt in build directory\n\t"
                    "Did you forget to do 'west build ...' first?\n\t"
                    "See: https://docs.zephyrproject.org/latest/develop/getting_started/index.html#build-the-blinky-sample"
                )

        # Check for installed udev rule
        UDEV_RULE_PATH = "/etc/udev/rules.d/60-openocd.rules"
        if sys.platform == "linux" and not os.path.exists(UDEV_RULE_PATH):
            self.err(
                "No installed udev rule file '" + UDEV_RULE_PATH + "' found\n\t"
                "https://docs.zephyrproject.org/latest/boards/infineon/cyw920829m2evk_02/doc/index.html#infineon-openocd-installation"
            )

        # Check for usbipd server if platform is WSL
        if sys.platform == "linux" and (
            platform.uname().release.endswith("-Microsoft")
            or platform.uname().release.endswith("microsoft-standard-WSL2")
        ):
            self.dbg("  -- WSL platform detected")
            try:
                # Execute ipconfig to find all network adapters on host
                result = subprocess.run(
                    ["ipconfig.exe"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                if result.returncode == 0:
                    ipv4_addresses = []
                    for line in result.stdout.decode("utf-8").splitlines():
                        m = re.search(
                            r".*IPv4.*: *([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})",
                            line,
                        )
                        if m:
                            ipv4_addresses.append(m.group(1))

                    usbipd_running = False
                    for addr in ipv4_addresses:
                        try:
                            self.dbg(
                                "     Searching for usbipd server at: "
                                + addr
                                + ":3240",
                                level=Verbosity.DBG_MORE,
                                end="",
                            )
                            s = socket.create_connection((addr, 3240), timeout=1)
                            self.dbg(" - connected", level=Verbosity.DBG_MORE, end="")

                            # Send OP_REQ_DEVLIST to usbipd server
                            s.setblocking(1)
                            s.settimeout(10)
                            OP_REQ_DEVLIST = bytes(
                                [0x01, 0x11, 0x80, 0x05, 0x00, 0x00, 0x00, 0x00]
                            )
                            s.send(OP_REQ_DEVLIST)

                            # Receive OP_REP_DEVLIST from usbipd server
                            OP_REP_DEVLIST = bytes()
                            while True:
                                chunk = s.recv(1024)
                                OP_REP_DEVLIST += chunk
                                if len(chunk) == 0:
                                    s.close()
                                    break

                            # Validate server response
                            if len(OP_REP_DEVLIST) >= 12:
                                resp = struct.unpack(">HHII", OP_REP_DEVLIST[0:12])
                                if (
                                    resp[0] == 0x0111
                                    and resp[1] == 0x0005
                                    and resp[2] == 0x00000000
                                ):
                                    self.dbg(
                                        " -",
                                        resp[3],
                                        "exported device(s)",
                                        level=Verbosity.DBG_MORE,
                                    )
                                    self.dbg(
                                        "       usbipd server found at "
                                        + addr
                                        + ":3240"
                                    )
                                    usbipd_running = True
                                    break
                                else:
                                    self.dbg(
                                        " - bad OP_REP_DEVLIST response",
                                        level=Verbosity.DBG_MORE,
                                        end="",
                                    )

                        except TimeoutError:
                            self.dbg(" - TIMEOUT", level=Verbosity.DBG_MORE, end="")

                        self.dbg("\n", level=Verbosity.DBG_MORE, end="")

                    if not usbipd_running:
                        self.err(
                            "WSL platform detected but no connectable usbipd server found\n\t"
                            "See: https://learn.microsoft.com/en-us/windows/wsl/connect-usb#install-the-usbipd-win-project"
                        )

                else:
                    self.err(
                        "Failed to get Windows network adapters using 'ipconfig.exe':\n\t"
                    )
                    self.dbg(result.stdout.decode("utf-8"), level=Verbosity.DBG_MORE)

            except FileNotFoundError:
                self.err("Couldn't find executable 'ipconfig.exe'")
