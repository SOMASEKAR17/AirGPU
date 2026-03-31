import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import pickle
from torch.utils.data import DataLoader, TensorDataset
import urllib.request
import os

df = pd.read_csv("dataset.csv")


X = df.iloc[:, :-1].values
y = df.iloc[:, -1].values


X = torch.tensor(X, dtype=torch.float32)
y = torch.tensor(y, dtype=torch.long)


X = X.view(-1, 1, 28, 28) / 255.0


dataset = TensorDataset(X, y)
loader = DataLoader(dataset, batch_size=64, shuffle=True)


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


class CNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv_layers = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2),

            nn.Conv2d(16, 32, 3, padding=1),
            nn.ReLU(),
            nn.MaxPool2d(2)
        )

        self.fc_layers = nn.Sequential(
            nn.Linear(32 * 7 * 7, 128),
            nn.ReLU(),
            nn.Linear(128, 10)
        )

    def forward(self, x):
        x = self.conv_layers(x)
        x = x.view(x.size(0), -1)
        return self.fc_layers(x)

model = CNN().to(device)


loss_fn = nn.CrossEntropyLoss()
optimizer = optim.Adam(model.parameters(), lr=0.001)


for epoch in range(5):
    total_loss = 0

    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)

        preds = model(images)
        loss = loss_fn(preds, labels)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        total_loss += loss.item()

    print(f"epoch {epoch}, loss {total_loss:.4f}")


with open("cnn_model.pkl", "wb") as f:
    pickle.dump(model.state_dict(), f)

print("Model saved as cnn_model.pkl")