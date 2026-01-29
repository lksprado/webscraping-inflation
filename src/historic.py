import os

import pandas as pd

path = "/media/lucas/Files/2.Projetos/webscraping-inflation/data/months"


dfs = []

files = os.listdir(path)

for file in files:
    filepath = os.path.join(path, file)
    df = pd.read_csv(filepath, sep=",", encoding="utf-8")
    dfs.append(df)

df_final = pd.concat(dfs, ignore_index=True)


df_final.to_csv(
    "/media/lucas/Files/2.Projetos/webscraping-inflation/data/consolidated.csv",
    sep=",",
    index=False,
)
