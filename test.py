import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import pickle

# load dataset
df = pd.read_csv("dataset.csv")

x = torch.tensor(df[['x']].values, dtype=torch.float32)
y = torch.tensor(df[['y']].values, dtype=torch.float32)

# model
class Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear = nn.Linear(1, 1)

    def forward(self, x):
        return self.linear(x)

model = Model()

# loss + optimizer
loss_fn = nn.MSELoss()
optimizer = optim.SGD(model.parameters(), lr=0.01)

# training loop
for epoch in range(500):
    pred = model(x)
    loss = loss_fn(pred, y)

    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    if epoch % 50 == 0:
        print(f"epoch {epoch}, loss {loss.item()}")

# save model as pickle
with open("model.pkl", "wb") as f:
    pickle.dump(model.state_dict(), f)

print("Model saved as model.pkl")