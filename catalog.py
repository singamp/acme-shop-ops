"""The shop's products and starting stock. No imports, so setup scripts can read it too."""

# (sku, name, category, price in cents, starting stock, "low" at or below)
PRODUCTS = [
    ("apples", "Apples, 1 kg", "produce", 349, 800, 30),
    ("bananas", "Bananas, bunch", "produce", 199, 800, 30),
    ("pumpkin", "Pumpkin", "produce", 499, 0, 5),
    ("milk", "Milk, 1 L", "dairy", 129, 600, 30),
    ("eggs", "Eggs, 12", "dairy", 389, 400, 20),
    ("cheese", "Cheddar, 200 g", "dairy", 459, 300, 16),
    ("bread", "Sourdough loaf", "bakery", 429, 350, 16),
    ("coffee", "Coffee beans, 250 g", "pantry", 899, 250, 12),
    ("rice", "Basmati rice, 1 kg", "pantry", 329, 400, 20),
    ("chocolate", "Dark chocolate bar", "sweets", 249, 500, 20),
    ("candy-corn", "Candy corn, 250 g", "sweets", 299, 300, 16),
    ("caramel-apple", "Caramel apple", "sweets", 349, 200, 12),
]
