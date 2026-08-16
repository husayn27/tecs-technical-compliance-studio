use std::sync::Mutex;

use tauri::{Manager, RunEvent, State};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct EngineState {
    endpoint: Mutex<String>,
    child: Mutex<Option<CommandChild>>,
}

// Keep the packaged desktop engine on a version-specific, predictable port.
// This gives the WebView a reliable fallback even if Tauri IPC initializes
// slowly on a newly installed Windows machine. Development continues to use
// port 8765.
const PACKAGED_ENGINE_PORT: u16 = 18765;

impl Default for EngineState {
    fn default() -> Self {
        Self {
            endpoint: Mutex::new(format!("http://127.0.0.1:{PACKAGED_ENGINE_PORT}")),
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
                let sidecar = app
                    .shell()
                    .sidecar("tecs-engine")?
                    .env("TECS_ENGINE_PORT", PACKAGED_ENGINE_PORT.to_string())
                    .env("TECS_VISION_MODEL_REPOSITORY", "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF:Q4_K_M");
                let (_events, child) = sidecar.spawn()?;
                let state = app.state::<EngineState>();
                *state.endpoint.lock().unwrap() =
                    format!("http://127.0.0.1:{PACKAGED_ENGINE_PORT}");
                *state.child.lock().unwrap() = Some(child);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building TECS Lighting Quotation");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let state = handle.state::<EngineState>();
            let child = state.child.lock().unwrap().take();
            if let Some(child) = child {
                let _ = child.kill();
            }
        }
    });
}
