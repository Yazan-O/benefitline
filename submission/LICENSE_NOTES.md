# License notes

Benefitline ships under the MIT License (`LICENSE` at the repository root, copyright 2026
Mohamad Yazan Sadoun). It calls policyengine-us, which is AGPL-3.0. This file records what that
means for a hosted deployment and what every third-party license says, quoted from the package
metadata installed on the build machine on 2026-09-13.

## The short version

policyengine-us is a separate library. Benefitline imports the released 2.0.4 wheel from PyPI and
does not modify it. Benefitline's own source is MIT and is published in full. The engine's source
is upstream, at the pinned version, and is linked below. If a reviewer reads the boundary
differently, the fallback written into `PLAN.md` line 89 applies: run the engine as a separate
service with its own AGPL notice.

## policyengine-us and the network-use clause

Verification (installed distribution, `policyengine_us-2.0.4.dist-info`):

- `importlib.metadata.metadata("policyengine-us")["License"]` is `None`. The license is declared
  only as a classifier: `License :: OSI Approved :: GNU Affero General Public License v3`.
- `policyengine_us-2.0.4.dist-info/licenses/LICENSE` begins:
  "GNU AFFERO GENERAL PUBLIC LICENSE / Version 3, 19 November 2007".

Section 13 of that file, quoted verbatim:

> 13. Remote Network Interaction; Use with the GNU General Public License.
>
> Notwithstanding any other provision of this License, if you modify the Program, your modified
> version must prominently offer all users interacting with it remotely through a computer network
> (if your version supports such interaction) an opportunity to receive the Corresponding Source of
> your version by providing access to the Corresponding Source from a network server at no charge,
> through some standard or customary means of facilitating copying of software.

What that requires of a hosted Benefitline:

1. The clause triggers on a modified Program. Benefitline does not modify policyengine-us. It
   installs `policyengine-us==2.0.4` from PyPI (`requirements.txt`) and imports it from
   `src/benefitline/engine.py`, which is the only file that touches the engine.
2. Because there is no modified version, section 13 does not by itself compel Benefitline to
   publish anything. The conservative posture is taken anyway: the engine version is printed beside
   every number in the product (`ENGINE_VERSION` in `engine.py` reads
   `importlib.metadata.version("policyengine-us")`), and the corresponding source of the exact
   version in use is available at the link below.
3. Corresponding source for the engine, at the pinned version:
   https://github.com/PolicyEngine/policyengine-us (tag `2.0.4`; package page
   https://pypi.org/project/policyengine-us/2.0.4/).
4. Benefitline's own source, which is the part under MIT, is published with the submission. The
   contested boundary for a hosted service is not section 13 but section 0: whether importing and
   linking an AGPL library makes the calling program a "work based on the Program". Reasonable
   readings differ, and nothing here depends on which one is right, because Benefitline publishes
   its whole source anyway and `PLAN.md` line 89 keeps the process-separation fallback available.
5. If any future change patches, vendors, or forks policyengine-us, section 13 does apply to that
   change and the modified source must be offered to every user of the hosted service. The rule for
   this repository is therefore: no local patch to the engine. Fixes go upstream, or the input is
   corrected on Benefitline's side of the boundary (this is what was done for `ssn_card_type`, an
   input Benefitline now sets explicitly rather than an engine change).

AGPL-3.0 also carries the ordinary GPL obligations for the library itself: the license text and
copyright notice travel with any redistribution of policyengine-us, and no additional restriction
may be placed on it. Benefitline does not redistribute the engine; deployments install it from
PyPI at build time.

## Third-party notices

Every row below was read from the installed distribution on the build machine. "Metadata License"
is the `License` field of the package metadata, "Classifier" is the license classifier, and the
last column names the license file that was opened.

| Package | Version | Metadata `License` | Classifier or expression | License file read |
|---|---|---|---|---|
| policyengine-us | 2.0.4 | `None` | `License :: OSI Approved :: GNU Affero General Public License v3` | `.dist-info/licenses/LICENSE`: "GNU AFFERO GENERAL PUBLIC LICENSE Version 3, 19 November 2007" |
| strands-agents | 1.55.1 | `Apache-2.0` | `License :: OSI Approved :: Apache Software License` | `.dist-info/licenses/LICENSE`: Apache License, Version 2.0 |
| strands-agents-tools | 0.8.8 | `Apache-2.0` | `License :: OSI Approved :: Apache Software License` | Apache License, Version 2.0 |
| bedrock-agentcore | 1.23.0 | `Apache-2.0` | `License :: OSI Approved :: Apache Software License` | `.dist-info/licenses/LICENSE.txt`: Apache License, Version 2.0 |
| pydantic | 2.11.7 | `None` | License-Expression `MIT`, classifier `License :: OSI Approved :: MIT License` | `.dist-info/licenses/LICENSE`: "The MIT License (MIT) / Copyright (c) 2017 to present Pydantic Services Inc. and individual contributors." |
| boto3 | 1.43.93 | `Apache-2.0` | no license classifier | `.dist-info/LICENSE` (not under `licenses/`): Apache License, Version 2.0 |
| python-dotenv | 1.1.1 | `BSD-3-Clause` | `License :: OSI Approved :: BSD License` | BSD 3-Clause |

Attribution notices carried by those packages:

- strands-agents `.dist-info/licenses/NOTICE`: "Copyright Amazon.com, Inc. or its affiliates. All
  Rights Reserved."
- bedrock-agentcore `.dist-info/licenses/NOTICE.txt` lists its own third-party dependencies: boto3
  and botocore (Apache-2.0), pydantic (MIT), uvicorn (BSD 3-clause).

## Data and content

- Program rules, amounts, and statute links come from policyengine-us and from the agency pages
  listed in `data/SOURCES.md`, each with its URL and fetch date. US federal and Oklahoma statutes,
  regulations, and agency forms are government works and are not covered by this repository's
  license.
- The demo households in `gallery/` are fictional. They are labeled fictional on screen and in the
  README. No real family's data is in this repository.

Verification command used for every row:

```
python -c "import importlib.metadata as m; md = m.metadata('<pkg>'); print(md['Version'], md['License'], md.get_all('Classifier'))"
```
