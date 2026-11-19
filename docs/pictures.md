# Pictures with ComfyUI on another PC

The roleplay server can ask ComfyUI, running on another computer on your home network, for a
picture of the moment in a chat or a portrait of a character. The server's own GPU stays free
for chat. Nothing goes outside your network.

## 1. Install ComfyUI on the PC

Follow ComfyUI's own install instructions for your graphics card (github.com/comfyanonymous/ComfyUI).
AMD cards on Windows are supported but need ComfyUI's AMD instructions, not the NVIDIA ones.
Check that it works on that PC first: open it in a browser there and make one picture.

## 2. Add a model

Put an image model (a `.safetensors` checkpoint) in ComfyUI's `models/checkpoints` folder. The
server asks for 1216×832 scenes and 832×1216 portraits, the sizes SDXL models are trained on,
so an SDXL checkpoint suits it best.

## 3. Let the server reach it

ComfyUI only listens to its own PC by default. Start it with `--listen 0.0.0.0` (or the
"listen on network" option in the desktop app) so the server can reach it on port 8188.

Then allow the server through Windows Firewall, and only the server, from an administrator
PowerShell on the PC (replace the address if your server's is different):

```powershell
New-NetFirewallRule -DisplayName "ComfyUI from roleplay server" -Direction Inbound -Protocol TCP -LocalPort 8188 -RemoteAddress 10.2.1.75 -Action Allow
```

ComfyUI has no password. Never forward port 8188 on your router or put it behind Cloudflare.

## 4. Keep nothing on the PC

The server never asks ComfyUI to save a picture. It uses ComfyUI's **Save Image (Websocket)**
node, which sends the finished picture straight back over the connection without writing a file,
and then deletes the job from ComfyUI's history so the prompt isn't kept there either. If that
node is missing, the server refuses to make pictures rather than let ComfyUI save them.

To add the node, if your ComfyUI doesn't have it yet: copy `websocket_image_save.py` from the
`custom_nodes` folder of ComfyUI's source (github.com/comfyanonymous/ComfyUI) into your ComfyUI's
own `custom_nodes` folder, then restart ComfyUI. Settings in the roleplay app says **Connected**
only once the node is there.

What still stays on the PC: the model files you installed, and whatever ComfyUI's console window
prints while it runs (job progress). Don't open ComfyUI's own page in a browser to run these
prompts by hand, since that page keeps its own copies.

## 4. Connect it

In the roleplay app, open **Settings → Pictures (ComfyUI)** and enter the PC's address, such as
`http://10.2.1.123:8188`. The panel says **Connected** and lists your models; pick one, or
leave it on **First one found**.

## Using it

- **Picture this** in a chat's scene panel makes a picture of the moment. It appears after the
  latest message and never goes into any prompt. **remove picture** deletes it.
- **Make a portrait** in a character's editor draws them from their description and keeps it
  as their avatar.
- Nobody whose card says they're under 18 is ever pictured.
- If the PC is off or asleep, the button says so and chat carries on as normal.
- The first picture after starting ComfyUI is slow while it loads the model.
