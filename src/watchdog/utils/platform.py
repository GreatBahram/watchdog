# Copyright 2011 Yesudeep Mangalapilly <yesudeep@gmail.com>
# Copyright 2012 Google, Inc & contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import sys

PLATFORM_WINDOWS = "windows"
PLATFORM_LINUX = "linux"
PLATFORM_BSD = "bsd"
PLATFORM_DARWIN = "darwin"
PLATFORM_UNKNOWN = "unknown"


def get_platform_name() -> str:
    if sys.platform.startswith("win"):
        return PLATFORM_WINDOWS

    if sys.platform.startswith("darwin"):
        return PLATFORM_DARWIN

    if sys.platform.startswith("linux"):
        return PLATFORM_LINUX

    if sys.platform.startswith(("dragonfly", "freebsd", "netbsd", "openbsd", "bsd")):
        return PLATFORM_BSD

    return PLATFORM_UNKNOWN


__platform__ = get_platform_name()


def is_linux() -> bool:
    return __platform__ == PLATFORM_LINUX


def is_bsd() -> bool:
    return __platform__ == PLATFORM_BSD


def is_darwin() -> bool:
    return __platform__ == PLATFORM_DARWIN


def is_windows() -> bool:
    return __platform__ == PLATFORM_WINDOWS
