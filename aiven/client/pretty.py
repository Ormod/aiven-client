# Copyright 2015, Aiven, https://aiven.io/
#
# This file is under the Apache License, Version 2.0.
# See the file `LICENSE` for details.

"""Pretty-print JSON objects and lists as tables"""

from __future__ import print_function
import datetime
import fnmatch
import json


def format_item(key, value):
    if isinstance(value, list):
        return ", ".join(format_item(None, entry) for entry in value)
    elif isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    elif isinstance(value, str):
        if key and key.endswith("_time") and value.endswith("Z") and "." in value:
            # drop microseconds from timestamps
            value = value.split(".", 1)[0] + "Z"
        # json encode strings, but if the input string is exactly the same
        # as the output without quotes we'll go with the original
        json_v = json.dumps(value)
        if json_v == '"{}"'.format(value):
            return value
        return json_v
    elif isinstance(value, datetime.datetime):
        return value.isoformat()

    return json.dumps(value)


def print_list(result):
    for item in result:
        print(format_item(None, item))


def print_table(result, drop_fields=None, table_layout=None):
    """print a list of dicts in a nicer table format"""
    if not result:
        return

    if not isinstance(result[0], dict):
        return print_list(result)

    drop_fields = set(drop_fields or [])

    def iter_values(key, value):
        if not isinstance(value, dict):
            yield key, value
            return
        for subkey, subvalue in value.items():
            for kv in iter_values((key + "." if key else "") + subkey, subvalue):
                yield kv

    # format all fields and collect their widths
    widths = {}
    formatted_values = []
    for item in result:
        formatted_row = {}
        formatted_values.append(formatted_row)
        for key, value in item.items():
            if key not in drop_fields:
                for subkey, subvalue in iter_values(key, value):
                    formatted_row[subkey] = format_item(subkey, subvalue)
                    widths[subkey] = max(len(subkey), len(formatted_row[subkey]), widths.get(subkey, 1))

    # default table layout is one row per item with sorted field names
    if table_layout is None:
        table_layout = sorted(widths)
    if not isinstance(table_layout[0], (list, tuple)):
        table_layout = [table_layout]

    horizontal_fields = table_layout[0]

    def longest(vf):
        lengths = [len(f.split(".", 1)[-1]) for f in formatted_row if fnmatch.fnmatch(f, vf)]
        return max(lengths) if lengths else 0

    vertical_width = max(longest(f) for f in table_layout[1:]) if len(table_layout) > 1 else 0
    print("  ".join(f.upper().ljust(widths[f]) for f in horizontal_fields))
    print("  ".join("=" * widths[f] for f in horizontal_fields))
    for row_num, formatted_row in enumerate(formatted_values):
        if len(table_layout) > 1 and row_num > 0:
            print("")
        print("  ".join(formatted_row.get(f, "").ljust(widths[f]) for f in horizontal_fields))
        for vertical_field in table_layout[1:]:
            for key, value in sorted(formatted_row.items()):
                if fnmatch.fnmatch(key, vertical_field):
                    print("    {:{}} = {}".format(key.split(".", 1)[-1], vertical_width, value))
