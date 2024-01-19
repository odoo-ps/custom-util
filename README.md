> [!WARNING]  
> This repository is for internal usage only. It will be eventually merged into [odoo/upgrade-util](https://github.com/odoo/upgrade-util). No support will be provided for this code and external contributions will not be accepted.



# 🧙‍🔧 PS-Tech Upgrade Custom Utils

This repository contains helper functions to facilitate the writing of upgrade scripts, specifically tailored towards custom Odoo modules.

## Installation

### Through odoo-bin
Once you have clone this repository locally, just start `odoo` with the `src` directory of this repo added to the `--upgrade-path` option.
```shell-session
$ ./odoo-bin --upgrade-path=/path/to/custom-util/src,/path/to/other/upgrade/script/directory [...]
```

### As a python package
On platforms where you dont manage odoo yourself, you can install this package via pip:
```shell-session
$ python3 -m pip install git+https://github.com/odoo-ps/custom-util@master
```
On [Odoo.sh](https://www.odoo.sh/) it is recommended to add it to the `requirements.txt` of your repository:
```
odoo_upgrade_custom_util @ git+https://github.com/odoo-ps/custom-util@master
```
## How to use them?
Once installed, the helpers are available in the `custom_util` package under the `odoo.upgrade` namespace. For example:
```py
from odoo.upgrade import custom_util

def migrate(cr, version):
    custom_util.edit_views(...)  # etc.
```
