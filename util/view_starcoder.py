import torch
from transformers import AutoTokenizer, AutoModel
import matplotlib.pyplot as plt
import numpy as np
from sklearn.preprocessing import StandardScaler

def get_layer_activations_and_attention(model, tokenizer, input_text):
    inputs = tokenizer(input_text, return_tensors='pt')
    with torch.no_grad():
        outputs = model(**inputs, output_attentions=True, output_hidden_states=True)
    hidden_states = outputs.hidden_states
    attentions = outputs.attentions
    return hidden_states, attentions

print("Initializing device...")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model_name = "bigcode/starcoder"
print("Loading tokenizer and model...")
tokenizer = AutoTokenizer.from_pretrained(model_name, token='KEY')
model = AutoModel.from_pretrained(model_name, token='KEY', attn_implementation="eager").to(device)
scaler = StandardScaler()
total_layers = model.config.num_hidden_layers
print(f"The model has {total_layers} layers.")
packet = "PACKET"
hidden_states, attentions = get_layer_activations_and_attention(model, tokenizer, packet)
cols = 8
num_rows = int(np.ceil(total_layers / cols))
fig, axes = plt.subplots(num_rows, cols, figsize=(24, num_rows * 4))
plt.subplots_adjust(hspace=0.4, wspace=0.4)

for ax in axes.flat[total_layers:]:
    ax.remove()

for i, layer_index in enumerate(range(total_layers)):
    ax = axes.flat[i]
    
    layer_activations = hidden_states[layer_index][0].mean(dim=-1).cpu().numpy()
    layer_activations_scaled = scaler.fit_transform(layer_activations.reshape(-1, 1)).flatten()
    layer_attentions_mean = attentions[layer_index][0].mean(0).mean(-1).cpu().numpy()
    layer_attentions_scaled = scaler.fit_transform(layer_attentions_mean.reshape(-1, 1)).flatten()
    token_positions = np.arange(len(layer_activations_scaled))
    ax.scatter(token_positions, layer_activations_scaled, color='blue', alpha=0.2, label='Activations' if i == 0 else "")
    ax.scatter(token_positions, layer_attentions_scaled, color='green', alpha=0.2, label='Attentions' if i == 0 else "")
    ax.set_title(f'Layer {layer_index + 1}', fontsize=8)
    ax.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    ax.tick_params(axis='x', labelsize=6)
    ax.tick_params(axis='y', labelsize=6)

    if i == 0:
        ax.legend()

plt.tight_layout()
plt.show()
