import pandas as pd


def hex_to_rgb(hex_color):
    """Convert hex color to RGB values"""
    hex_color = hex_color.lstrip("#")
    return [int(hex_color[i : i + 2], 16) for i in (0, 2, 4)]


# Read the Excel file
df = pd.read_excel("LEGO+Colours+-+Pantone,+HSL+and+HEX+Values.xlsx", skiprows=4)

# Create dictionary with LEGO name as key and color values as object
lego_colors = {}
for _, row in df.iterrows():
    # Get the values using column names
    lego_name = row["Bricklink Name (Lego Name)"]
    h = row["H"]
    s = row["S"]
    l = row["L"]
    pantone = row["Nearest Pantone"]
    hex_color = row["Hex"]

    # Skip rows with NaN values
    if (
        pd.isna(lego_name)
        or pd.isna(h)
        or pd.isna(s)
        or pd.isna(l)
        or pd.isna(hex_color)
    ):
        continue

    # Convert HSL values to integers
    try:
        h = int(float(h))
        # Convert S and L from decimal to percentage (0-100)
        s = int(float(s) * 100)
        l = int(float(l) * 100)

        # Convert hex to RGB
        rgb = hex_to_rgb(hex_color)

        # Create color object with all values
        lego_colors[str(lego_name)] = {
            "hsl": [h, s, l],
            "rgb": rgb,
            "pantone": str(pantone) if not pd.isna(pantone) else None,
            "hex": hex_color,
        }
    except (ValueError, TypeError):
        continue

# Print the dictionary in a format that can be used in Python
print("lego_colors = {")
for name, color_data in lego_colors.items():
    print(f"    '{name}': {{")
    print(f"        'hsl': {color_data['hsl']},")
    print(f"        'rgb': {color_data['rgb']},")
    print(f"        'pantone': {repr(color_data['pantone'])},")
    print(f"        'hex': '{color_data['hex']}'")
    print("    },")
print("}")
