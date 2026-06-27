# Minimal module skeleton (copy-paste)

A complete `library` module that installs clean on **Odoo 17/18/19**: one model,
its ACL, and a menu → action → form/list view. Replace `library` /
`library.book` throughout.

> **v18 vs v17**: the list root is `<list>` and the action `view_mode` uses
> `list` in v18 (v17 used `<tree>` / `tree,form`). The XML below is v18.

```
library/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   └── library_book.py
├── security/
│   └── ir.model.access.csv
└── views/
    └── library_book_views.xml
```

## `library/__manifest__.py`

```python
{
    'name': 'Library',
    'version': '18.0.1.0.0',
    'category': 'Services/Library',
    'summary': 'Manage books',
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',   # security before the views that need it
        'views/library_book_views.xml',
    ],
    'application': True,
    'license': 'LGPL-3',
}
```

## `library/__init__.py`

```python
from . import models
```

## `library/models/__init__.py`

```python
from . import library_book
```

## `library/models/library_book.py`

```python
from odoo import fields, models


class LibraryBook(models.Model):
    _name = 'library.book'
    _description = 'Library Book'
    _order = 'name'

    name = fields.Char(string='Title', required=True)
    author = fields.Char()
    isbn = fields.Char(string='ISBN')
    state = fields.Selection(
        [('available', 'Available'), ('borrowed', 'Borrowed')],
        default='available', required=True,
    )
    active = fields.Boolean(default=True)   # enables archive out of the box
```

## `library/security/ir.model.access.csv`

`model_id:id` is `model_` + the model name with dots as underscores
(`library.book` → `model_library_book`). Without a row here, non-admin users
hit AccessError and the model looks "broken" only for them.

```csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_library_book_user,library.book.user,model_library_book,base.group_user,1,1,1,1
```

## `library/views/library_book_views.xml`

Order inside the file matters less than manifest order, but define the action
before the menu that references it.

```xml
<?xml version="1.0" encoding="utf-8"?>
<odoo>
    <record id="action_library_book" model="ir.actions.act_window">
        <field name="name">Books</field>
        <field name="res_model">library.book</field>
        <field name="view_mode">list,form</field>   <!-- v17: tree,form -->
    </record>

    <record id="view_library_book_list" model="ir.ui.view">
        <field name="name">library.book.list</field>
        <field name="model">library.book</field>
        <field name="arch" type="xml">
            <list>                                   <!-- v17: <tree> -->
                <field name="name"/>
                <field name="author"/>
                <field name="state"/>
            </list>
        </field>
    </record>

    <record id="view_library_book_form" model="ir.ui.view">
        <field name="name">library.book.form</field>
        <field name="model">library.book</field>
        <field name="arch" type="xml">
            <form>
                <header>
                    <field name="state" widget="statusbar"/>
                </header>
                <sheet>
                    <group>
                        <field name="name"/>
                        <field name="author"/>
                        <field name="isbn"/>
                    </group>
                </sheet>
            </form>
        </field>
    </record>

    <menuitem id="menu_library_root" name="Library"/>
    <menuitem id="menu_library_book"
              parent="menu_library_root"
              action="action_library_book"/>
</odoo>
```

## Install / update

```bash
odoo-bin -d DB -i library          # first install
odoo-bin -d DB -u library          # re-apply after editing data/views XML
```

`-i` on an already-installed DB does **not** reload changed data/views — use
`-u`. Add `--dev=xml` in development to reload view XML from disk without `-u`.

## Adding chatter (optional)

To get messaging/activities, make the model inherit `mail.thread` /
`mail.activity.mixin` (`_inherit = ['mail.thread', 'mail.activity.mixin']`,
`depends` += `'mail'`) and add `<chatter/>` after `</sheet>` in v18 (v17 uses
`<div class="oe_chatter">…</div>`). See the `odoo-views` skill.
