import torch
import torch.nn as nn
import torch.optim as optim

# dataset (y = 2x + 1 pattern with slight noise)
x = torch.tensor([[1.0], [2.0], [3.0], [4.0], [5.0]])
y = torch.tensor([[3.1], [5.0], [7.2], [9.1], [11.0]])

# model
class Model(nn.Module):
    def __init__(self):
        super().__init__()
        self.linear_layer = nn.Linear(1, 1)

    def forward(self, x):
        return self.linear_layer(x)

model = Model()

# loss and optimizer
loss_fn = nn.MSELoss()
optimizer = optim.SGD(model.parameters(), lr=0.01)

# training loop
for epoch in range(500):
    predictions = model(x)
    loss = loss_fn(predictions, y)

    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    if epoch % 50 == 0:
        print(f"epoch {epoch}, loss {loss.item()}")

# testing
test_input = torch.tensor([[6.0]])
predicted_output = model(test_input)

print("\nPrediction for x=6:", predicted_output.item())