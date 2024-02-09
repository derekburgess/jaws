import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, accuracy_score

file_path = './data/sets/packets_a.csv'
data = pd.read_csv(file_path, low_memory=False)
data = data[data['dst_ip'] != 'ADDR'] # Remove specific IP address

features = ['dst_port', 'size']
target = 'src_ip'

X = data[features]
y = data[target]

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.8, random_state=42)
clf = RandomForestClassifier(n_estimators=500, random_state=42)
clf.fit(X_train, y_train)
y_pred = clf.predict(X_test)

print("Accuracy:", accuracy_score(y_test, y_pred))
print("\nClassification Report:\n", classification_report(y_test, y_pred, zero_division=1))
