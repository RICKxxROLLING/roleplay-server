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

## 5. Optional: start ComfyUI only when needed

ComfyUI keeps its model in the graphics card's memory while it runs. To have it start when a
picture is asked for and stop after 15 idle minutes, run the small helper in
`deploy/comfy-waker` on the PC instead of leaving ComfyUI open:

1. Copy `comfy_waker.py` and `comfy_waker.example.json` to a folder on the PC, and rename the
   JSON to `comfy_waker.json`. Fill in the command that starts your ComfyUI: for ComfyUI Desktop,
   its install's Python, `ComfyUI\main.py`, `--listen 0.0.0.0 --port 8188`, and the same model,
   input and output folders the Desktop app passes (Task Manager's Details tab shows them while
   Desktop runs it). Keep `"allow"` to the server's address.
2. Allow the server to reach it, from an administrator PowerShell:
   ```powershell
   New-NetFirewallRule -DisplayName "ComfyUI helper from roleplay server" -Direction Inbound -Protocol TCP -LocalPort 8190 -RemoteAddress 10.2.1.75 -Action Allow
   ```
3. Start it with Windows, hidden (an ordinary PowerShell is enough). Use any Python on the PC;
   ComfyUI's own `pythonw.exe` has no window:
   ```powershell
   $py = "D:\Comfy-Desktop\ComfyUI-Installs\ComfyUI\ComfyUI\.venv\Scripts\pythonw.exe"
   $act = New-ScheduledTaskAction -Execute $py -Argument '"D:\Comfy-Desktop\comfy-waker\comfy_waker.py"' -WorkingDirectory "D:\Comfy-Desktop\comfy-waker"
   Register-ScheduledTask -TaskName "ComfyUI helper" -Action $act -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Settings (New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0)
   ```
4. In the roleplay app, enter the helper's address (`http://10.2.1.123:8190`) under the ComfyUI
   address in **Settings → Pictures**. While ComfyUI is stopped the panel says so, and the first
   picture after that takes longer: about 17 s to start, plus loading the model.

The helper never stops a ComfyUI it didn't start, so opening the Desktop app yourself still
works. Close the Desktop app while the helper runs, though, or the two will both want port 8188.

## 6. Connect it

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
