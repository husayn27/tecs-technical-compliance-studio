use tauri_plugin_shell::ShellExt;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            #[cfg(not(debug_assertions))]
            {
                let sidecar = app
                    .shell()
                    .sidecar("tecs-engine")?
                    .env("TECS_VISION_MODEL_REPOSITORY", "ggml-org/Qwen2.5-VL-7B-Instruct-GGUF:Q4_K_M");
                let (_events, _child) = sidecar.spawn()?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running TECS Lighting Quotation");
}
