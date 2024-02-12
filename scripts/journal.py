"""
Interface to electronic lab journal https://elabftw.tugraz.at/
See https://alexgu2008.github.io/elabftw_api_support/
"""

import elabapy
import json
import os
from sys import argv

ELAB_URL = os.environ.get("ELAB_URL")
ELAB_KEY = os.environ.get("ELAB_KEY")

manager = elabapy.Manager(endpoint=ELAB_URL, token=ELAB_KEY)

def main():
    command = argv[1]
    if command == "log":
        create_experiment(code = argv[2], title = argv[3])
    else:
        print("Usage: journal.py log <code> <title>")


def create_experiment(code, title):
    experiment = manager.create_experiment()
    content = {"title": title, "metadata": json.dumps({
               "extra_fields": {
                   "code": {
                        "type": "text",
                        "value": code
                   }
               }})
               }
    print(manager.post_experiment(experiment["id"], content))


if __name__ == "__main__":
    main()
