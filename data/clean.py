import pandas as pd
import yfinance as yf

df=pd.read_csv('nifty_500.csv')
print(df.head())

print("count--"+str(len(df)))
print("duplicates--"+str(df.duplicated().any()))

df["Ticker"]=df['Symbol'].astype(str)+'.NS'


#print(df['Ticker'][2])

#data=yf.ticker.Ticker(df['Ticker'][2])

#print(data.history(period='1d'))

count=0
wrong_ticker=[]

""" for i in (df['Ticker']):
    try:
        data=yf.Ticker(i)
        hist=data.history(period='1d')
        if hist.empty:
            wrong_ticker.append(i)
            count+=1
    except Exception as e:
        print("Error for ticker {}: {}".format(i, e))
        wrong_ticker.append(i)
        count+=1 """
for i in (df['Industry']):
    if pd.isnull(i):
        count+=1
        wrong_ticker.append(i)

print("Count of wrong tickers: {}".format(count))
print("Wrong tickers: {}".format(wrong_ticker))

df_final=df[['Company Name','Industry','Ticker']]
df_final = df_final.rename(columns={'Company Name': 'name', 'Industry': 'sector', 'Ticker': 'ticker'})
print(df_final.head())

save_path='nifty_500_cleaned.csv'
df_final.to_csv(save_path, index=False)