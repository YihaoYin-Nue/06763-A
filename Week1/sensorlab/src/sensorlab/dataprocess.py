import numpy as np
import pandas as pd
import io
import urllib.request
import zipfile
from pathlib import Path

URL = 'https://archive.ics.uci.edu/static/public/360/air+quality.zip'

def load(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        print(f'fetching {URL}')
        with urllib.request.urlopen(URL) as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            path.write_bytes(archive.read('AirQualityUCI.csv'))

    return pd.read_csv(path, sep=';', decimal=',')

def clean(df):
    df = (df.dropna(axis=1, how='all').dropna(how='all'))

    df['ts'] = pd.to_datetime(
        df['Date'] + ' ' + df['Time'].str.replace('.', ':', regex=False),
        format='%d/%m/%Y %H:%M:%S',
    )
    df = df.replace(-200, np.nan)

    return df
