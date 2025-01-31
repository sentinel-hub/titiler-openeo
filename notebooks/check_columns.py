import pandas as pd

# Read the Excel file
df = pd.read_excel('LEGO+Colours+-+Pantone,+HSL+and+HEX+Values.xlsx')

# Print column names
print("Column names in the Excel file:")
for col in df.columns:
    print(f"- {col}")

# Print first few rows to understand the data structure
print("\nFirst few rows of data:")
print(df.head())
