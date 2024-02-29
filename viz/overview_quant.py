import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
import matplotlib.pyplot as plt
import numpy as np
from sklearn.preprocessing import StandardScaler

def get_layer_activations_and_attention(model, tokenizer, packet):
    inputs = tokenizer(packet, return_tensors='pt').to(device)
    with torch.no_grad():
        outputs = model(**inputs, output_attentions=True, output_hidden_states=True)
    hidden_states = outputs.hidden_states
    attentions = outputs.attentions
    return hidden_states, attentions

print("Initializing device...")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
quantization_config = BitsAndBytesConfig(load_in_8bit=True) # to use 4bit use `load_in_4bit=True` instead
checkpoint = "bigcode/starcoder2-15b"
print("Loading tokenizer and model...")
tokenizer = AutoTokenizer.from_pretrained(checkpoint)
model = AutoModelForCausalLM.from_pretrained(checkpoint, quantization_config=quantization_config)
scaler = StandardScaler()
total_layers = model.config.num_hidden_layers

packet = "PACKET"

hidden_states, attentions = get_layer_activations_and_attention(model, tokenizer, packet)
user_choice = input(f'Enter "ALL" to visualize all layers or specify a single layer number(1-{total_layers}): ')

def plot_specific_or_all_layers(choice):
    if choice.upper() == "ALL":
        cols = 8
        num_rows = int(np.ceil(total_layers / cols))
        fig, axes = plt.subplots(num_rows, cols, figsize=(24, num_rows * 4))
        plt.subplots_adjust(hspace=0.4, wspace=0.4)

        for ax in axes.flat[total_layers:]:
            ax.remove()

        for i, layer_index in enumerate(range(total_layers)):
            ax = axes.flat[i]
            plot_layer(ax, layer_index, i == 0)

        plt.tight_layout()
        plt.show()
    elif choice.isdigit() and 1 <= int(choice) <= total_layers:
        layer_index = int(choice) - 1
        fig, ax = plt.subplots(figsize=(12, 12))
        plot_layer(ax, layer_index, True)
        plt.tight_layout()
        plt.show()
    else:
        print("Invalid choice. Please enter 'ALL' or a valid layer number...")

def plot_layer(ax, layer_index, show_legend):
    layer_activations = hidden_states[layer_index][0].mean(dim=-1).cpu().numpy()
    layer_activations_scaled = scaler.fit_transform(layer_activations.reshape(-1, 1)).flatten()
    layer_attentions_mean = attentions[layer_index][0].mean(0).mean(-1).cpu().numpy()
    layer_attentions_scaled = scaler.fit_transform(layer_attentions_mean.reshape(-1, 1)).flatten()
    token_positions = np.arange(len(layer_activations_scaled))
    ax.scatter(token_positions, layer_activations_scaled, color='blue', alpha=0.2, label='Hidden States' if show_legend else "")
    ax.scatter(token_positions, layer_attentions_scaled, color='green', alpha=0.2, label='Attentions' if show_legend else "")
    ax.set_title(f'Layer {layer_index + 1}', fontsize=8)
    ax.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    ax.tick_params(axis='x', labelsize=6)
    ax.tick_params(axis='y', labelsize=6)
    if show_legend:
        ax.legend()
    ax.set_ylim(-2,2)

plot_specific_or_all_layers(user_choice)

"""
def plot_layer(ax, layer_index, show_legend):
    layer_activations = hidden_states[layer_index][0].mean(dim=-1).cpu().numpy()
    layer_activations_scaled = scaler.fit_transform(layer_activations.reshape(-1, 1)).flatten()
    layer_attentions = attentions[layer_index][0].cpu().numpy()  # Get all attention heads
    token_positions = np.arange(len(layer_activations_scaled))
    
    # Plotting each attention head separately
    for head_index, attention_head in enumerate(layer_attentions):
        attention_head_scaled = scaler.fit_transform(attention_head.mean(-1).reshape(-1, 1)).flatten()
        ax.scatter(token_positions, attention_head_scaled, alpha=0.2, label=f'Head {head_index+1}' if show_legend else "", marker='.')
    
    ax.set_title(f'Layer {layer_index + 1}', fontsize=8)
    ax.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    ax.tick_params(axis='x', labelsize=6)
    ax.tick_params(axis='y', labelsize=6)
    #if show_legend:
        #ax.legend()
    ax.set_ylim(-2, 2)
"""

