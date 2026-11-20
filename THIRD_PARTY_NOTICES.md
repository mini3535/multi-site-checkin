# Third-party notices

## Playwright Chromium seccomp profile

`seccomp_profile.json` is vendored from the official Playwright repository. It
retains the complete upstream profile and explicitly permits the `clone`,
`setns`, and `unshare` syscalls needed to create user namespaces for Chromium's
sandbox. The Playwright `v1.65.2` source tag was unavailable when this file was
pinned; the Python package's bundled driver is `v1.65.1`, so the profile is
copied from that matching official release tag at commit
`bbd948344ea9a60bf1e6f125c02ca99b2b51593b`. This profile's SHA-256 matches
the adjacent `v1.65.0` release as well. The profile is used with the Playwright
Python `chromium_sandbox=True` launch option. No `SYS_ADMIN` capability or
unconfined seccomp setting is enabled.

- Project: Microsoft Playwright
- Repository: https://github.com/microsoft/playwright
- Source file: `utils/docker/seccomp_profile.json`
- Pinned source: https://github.com/microsoft/playwright/blob/bbd948344ea9a60bf1e6f125c02ca99b2b51593b/utils/docker/seccomp_profile.json
- SHA-256: `cc3e61cabda6bbc1e53e54d27ba4d55a9d3be829b6dd1a596f4a7b31b1cc7849`
- License: Apache-2.0; full license text is in [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).

## Enshan daily check-in reference

The request sequence and formhash extraction behavior in
`autosign/site_adapters.py` were informed by the following MIT-licensed project.
The adapter was rewritten for this application's fixed-origin transport, bounded
responses, login/challenge classification and credential-safe logging. The
upstream project's automatic TLS-warning suppression is not used.

- Project: Sitoi/dailycheckin
- Repository: https://github.com/Sitoi/dailycheckin
- Referenced commit: `135cc236e59ac4f14bcddc44acb3cbb3ed6010b5`
- Referenced file: `dailycheckin/enshan/main.py`
- Reference URL: https://github.com/Sitoi/dailycheckin/blob/135cc236e59ac4f14bcddc44acb3cbb3ed6010b5/dailycheckin/enshan/main.py
- License source: https://github.com/Sitoi/dailycheckin/blob/135cc236e59ac4f14bcddc44acb3cbb3ed6010b5/LICENSE

### MIT License

Copyright (c) 2021 Sitoi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
