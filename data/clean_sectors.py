import yfinance as yf
import pandas as pd
import numpy as np

df=pd.read_csv("nifty_500_cleaned.csv")

print(df.columns)

print(df['sector'].unique())

print(df['sector'][df['name']=='HDFC Bank Ltd.'])

data=yf.Ticker('NIFTYPVTBANK.NS')
hist=data.history(period='1d')
print(hist)