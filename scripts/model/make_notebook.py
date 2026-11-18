"""Build the Kaggle notebook (train_everyball.ipynb) from train_everyball.py + everyball_net.py.
python3 scripts/model/make_notebook.py  -> scripts/model/train_everyball.ipynb (+ train_everyball_full.py, same code)"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "train_everyball.py"), encoding="utf-8").read()
net = open(os.path.join(HERE, "everyball_net.py"), encoding="utf-8").read()
full = src.replace("# NET_CODE_HERE", net)
open(os.path.join(HERE, "train_everyball_full.py"), "w", encoding="utf-8").write(full)
cells = []
for part in re.split(r"^# %%", full, flags=re.M)[1:]:
    md = part.startswith(" [markdown]")
    body = part.split("\n", 1)[1] if "\n" in part else ""
    body = body.strip("\n")
    if md:
        body = "\n".join(l[2:] if l.startswith("# ") else l.lstrip("#") for l in body.splitlines())
        cells.append({"cell_type": "markdown", "metadata": {}, "source": body.splitlines(True)})
    else:
        cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": body.splitlines(True)})
nb = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                                   "language_info": {"name": "python"}}, "nbformat": 4, "nbformat_minor": 5}
json.dump(nb, open(os.path.join(HERE, "train_everyball.ipynb"), "w", encoding="utf-8"), indent=1)
print("wrote train_everyball.ipynb with", len(cells), "cells")
