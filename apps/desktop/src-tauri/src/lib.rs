use std::{net::TcpListener, sync::Mutex};

use tauri::{Manager, RunEvent, State};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct EngineState {
    endpoint: Mutex<String>,
    child: Mutex<Option<CommandChild>>,
}

impl Default for EngineState {
    fn default() -> Self {
        Self {
            endpoint: Mutex::new("http://127.0.0.1:8765".to_string()),
            child: Mutex::new(None),
        }
    }
}

#[tauri::command]
fn engine_endpoint(state: State<'_, EngineState>) -> String {
    state.endpoint.lock().unwrap().clone()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(EngineState::default())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![engine_endpoint])
        .setup(|app| {
            #[cfg(not(debug_assertions))]
            {
                // A private per-launch port prevents a leftover engine from an older
                // installation from being mistaken for the engine bundled with this UI.
                let listener = TcpListener::bind("127.0.0.1:0")?;
                let port = listener.local_addr()?.port();
                drop(listener);

                let sidecar = app
                    .shell()
                    .sidecar("tecs-engine")?
                    .env("TECS_ENGINE_PORT", port.to_string())
                    .env("TECS_VISION_MODEL_REPOSITORY", "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF:Q4_K_M");
                let (_events, child) = sidecar.spawn()?;
                let state = app.state::<EngineState>();
                *state.endpoint.lock().unwrap() = format!("http://127.0.0.1:{port}");
                *state.child.lock().unwrap() = Some(child);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building TECS Lighting Quotation");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let state = handle.state::<EngineState>();
            if let Some(child) = state.child.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
