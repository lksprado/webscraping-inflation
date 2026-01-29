import yaml

with open("src/config.yml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

kws = cfg["keywords"]

for item in kws[:1]:
    keyword = item["name"]
    print(keyword)
