import pandas as pd

df = pd.read_json('laptoplk_products.json')
df.to_csv('products.csv', index=False)