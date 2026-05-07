import gradio as gr
import mtdna_backend
import json
import data_preprocess, model, pipeline
import os
import hashlib
import threading
import asyncio
import mtdna_backend
# Gradio UI
#stop_flag = gr.State(value=False)
class StopFlag:
    def __init__(self):
        self.value = False
global_stop_flag = StopFlag()  # Shared between run + stop

with open("better_offer.html", "r", encoding="utf-8") as f:
    pricing_html = f.read()

with open("mtdna_tool_explainer_updated.html", "r", encoding="utf-8") as f:
    flow_chart = f.read()

css = """
/* The main container for the entire NPS section */
#nps-container {
    background-color: #333;
    padding: 20px;
    border-radius: 8px;
    display: flex;
    flex-direction: column;
    width: 100%;
}

/* Ensure the question text is properly spaced */
#nps-container h3 {
    color: #fff;
    margin-bottom: 20px; /* Space between question and buttons */
    text-align: center; /* Center the question text */
}

/* Flexbox container for the radio buttons */
#nps-radio-container {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;
}

/* Ensure the inner Gradio radio group stretches to fill the container */
#nps-radio-container > div.gr-radio-group {
    width: 100% !important;
    display: flex !important;
    justify-content: space-between !important;
}

/* Styling for each individual button */
#nps-radio-container .gr-radio-label {
    display: flex;
    justify-content: center;
    align-items: center;
    width: 35px;
    height: 35px;
    border-radius: 4px;
    background-color: #555;
    color: white;
    font-weight: bold;
    cursor: pointer;
    transition: background-color 0.2s ease;
    font-size: 14px;
    margin: 0; /* Remove default button margins */
}

#nps-radio-container .gr-radio-label:hover {
    background-color: #777;
}

#nps-radio-container input[type="radio"]:checked + .gr-radio-label {
    background-color: #999;
    border: 2px solid white;
}

#nps-radio-container .gr-radio-input {
    display: none;
}

/* The row for the "Not likely" and "Extremely likely" labels */
#nps-labels-row {
    display: flex;
    justify-content: space-between;
    margin-top: 15px; /* Adds space below the number buttons */
    width: 100%; /* Force labels row to take full width */
}

#nps-labels-row .gr-markdown p {
    margin: 0;
    font-size: 1.0em;
    color: #ccc;
    white-space: nowrap;
    width: 50%;
}

#nps-labels-row .gr-markdown:first-child p {
    text-align: left;
}

#nps-labels-row .gr-markdown:last-child p {
    text-align: right;
}

/* Submit button styling */
#nps-submit-button {
    margin-top: 25px; /* Adds space above the submit button */
    width: 100%;
}

#nps-submit-button:active {
    border-color: white !important;
    box-shadow: 0 0 5px white inset;
}
#nps-radio-container .wrap {
    display: grid !important;
    grid-template-columns: repeat(11, 1fr); /* 11 equal slots */
    gap: 8px; /* spacing between buttons */
}

#niche-input-box {
    display: none;
}

"""

with gr.Blocks(css=css) as interface:
    # with gr.Tab("CURIOUS ABOUT THIS PRODUCT?"):
    #     gr.HTML(value=pricing_html)
    with gr.Tab("🧬 BioMetadataAudit"):
        gr.Markdown("# 🧬 BioMetadataAudit (MVP)")
        #inputMode = gr.Radio(choices=["Single Accession", "Batch Input"], value="Single Accession", label="Choose Input Mode")
        user_email = gr.Textbox(label="📧 Your email (used to track free quota). ",
                                placeholder="Enter your email and click Submit and Classify button below to run accessions.\nYou'll get +20 extra free queries and can download the results.")
            
    
        usage_display = gr.Markdown("", visible=False)
    
        raw_text = gr.Textbox(
            label="Accession ID(s)",
            placeholder=(
                "Enter accession IDs (one per line or comma-separated).\n"
                "Accepts BioProject, BioSample, GenBank accession, or SRR/SRX.\n"
                "Examples: PRJNA976261   SAMN23469632   OL757400   SRR17084312"
            ),
        )
        #niche_input = gr.Textbox(visible=False, elem_id="niche-input-box")
        niche_input = gr.Textbox(visible=True, elem_id="niche-input-box", interactive=False)

        gr.HTML("""
        <div style="margin-top: 10px; line-height: 1.8;">
          <a href="https://docs.google.com/spreadsheets/d/1lKqPp17EfHsshJGZRWEpcNOZlGo3F5qU/edit?usp=sharing" 
             target="_blank" style="display:block; margin-bottom: 8px;">
             Example Excel Input Template
          </a>
        
          <a href="#" id="no-dataset-link" style="color:#4EA8DE; text-decoration: underline; display:block; margin-bottom: 8px;">
            I don't have a dataset to test — where should I find it?
          </a>
        
          <a href="#" id="custom-label-link" style="color:#4EA8DE; text-decoration: underline; display:block; margin-bottom: 8px;">
            Customize your label
          </a>
        
          <!-- Box for instructions -->
          <div id="instruction-box" 
               style="display:none; background:#2b2b2b; color:white; padding:10px; border-radius:8px; margin-top:8px; position: relative; max-width: 600px;">
            <span id="close-instruction" 
                  style="position:absolute; top:5px; right:10px; cursor:pointer; font-weight:bold;">✕</span>
            <p style="margin:0;">
              <strong>Quick collect:</strong> type 
              <code>homo sapiens</code> [or any organism] 
              AND <code>mitochondrion</code> AND <code>&lt;country_name&gt;</code> 
              on <a href="https://www.ncbi.nlm.nih.gov/nuccore" target="_blank" style="color:#4EA8DE;">NCBI</a>.
            </p>
          </div>
        
          <!-- Customize Label Box -->
          <div id="custom-label-box" 
               style="display:none; background:#2b2b2b; color:white; padding:12px; border-radius:8px; margin-top:8px; position: relative; max-width: 600px;">
            <span id="close-custom-label" 
                  style="position:absolute; top:5px; right:10px; cursor:pointer; font-weight:bold;">✕</span>
        
            <label for="niche-dropdown" style="display:block; margin-bottom:6px;">Choose your label:</label>
            <select id="niche-dropdown" style="width:100%; padding:8px; border-radius:5px; border:none; background:#3b3b3b; color:white;">
              <option value="">-- Select a label --</option>
              <option value="ethnicity">Ethnicity</option>
              <option value="specific location">Specific Location</option>
              <option value="phenotype">Phenotype</option>
              <option value="haplogroup">Haplogroup</option>
              <option value="contact">Contact (for custom label)</option>
            </select>
        
            <p id="selected-label" style="margin-top:10px; color:#ddd;">No label selected</p>
          </div>
        </div>
        
        <script>

        function waitForElement(selector, callback) {
          const observer = new MutationObserver(() => {
            const el = document.querySelector(selector);
            if (el) {
              observer.disconnect();
              callback(el);
            }
          });
          observer.observe(document.body, { childList: true, subtree: true });
        }
        
        // Run when Gradio textbox appears
        waitForElement('#niche-input-box textarea', (hiddenBox) => {
          console.log("✅ Hidden textbox detected:", hiddenBox);
        
          const customLabelLink = document.getElementById('custom-label-link');
          const customLabelBox = document.getElementById('custom-label-box');
          const closeCustomLabel = document.getElementById('close-custom-label');
          const dropdown = document.getElementById('niche-dropdown');
          const display = document.getElementById('selected-label');
          let selectedValue = null;

          const noDatasetLink = document.getElementById('no-dataset-link');
          const instructionBox = document.getElementById('instruction-box');
          const closeInstruction = document.getElementById('close-instruction');
        
          // Show instruction box when link clicked
          noDatasetLink.addEventListener('click', (e) => {
            e.preventDefault();
            instructionBox.style.display = 'block';
          });
        
          // Close the instruction box
          closeInstruction.addEventListener('click', () => {
            instructionBox.style.display = 'none';
          });
        
          // Toggle open
          customLabelLink.addEventListener('click', (e) => {
            e.preventDefault();
            customLabelBox.style.display = 'block';
          });
        
          // Close button with optional warning
          closeCustomLabel.addEventListener('click', () => {
            if (selectedValue) {
              const confirmClose = confirm(
                `You selected "${selectedValue}". Closing will erase your choice. Continue?`
              );
              if (!confirmClose) return;
            }
            selectedValue = null;
            customLabelBox.style.display = 'none';
            hiddenBox.value = "";
            hiddenBox.dispatchEvent(new Event('input', { bubbles: true }));
            display.textContent = "No label selected";
          });
        
          // Handle dropdown changes
          dropdown.addEventListener('change', () => {
            const value = dropdown.value.trim().toLowerCase();
            selectedValue = value;
        
            if (value && value !== "contact") {
              hiddenBox.value = value;
              hiddenBox.dispatchEvent(new Event('input', { bubbles: true }));
              display.textContent = `✅ Selected Label: ${value}`;
            } else if (value === "contact") {
              hiddenBox.value = "";
              hiddenBox.dispatchEvent(new Event('input', { bubbles: true }));
              display.innerHTML = `📧 Please <a href="mailto:khanhphungvy@gmail.com" style="color:#4EA8DE;">contact us</a> for a custom label.`;
            } else {
              hiddenBox.value = "";
              hiddenBox.dispatchEvent(new Event('input', { bubbles: true }));
              display.textContent = "No label selected";
            }
        
            console.log("📤 Synced value to hidden textbox:", hiddenBox.value);
          });
        });
        </script>
        """)


        file_upload = gr.File(label="📁 Or Upload Excel File", file_types=[".xlsx"], interactive=True)
        processed_info = gr.Markdown(visible=False)  # new placeholder for processed list
        
        with gr.Row():
            run_button = gr.Button("▶ Run Audit", elem_id="run-btn")
            stop_button = gr.Button("❌ Stop Batch", visible=False, elem_id="stop-btn")
            reset_button = gr.Button("🔄 Reset", elem_id="reset-btn")

    
        status = gr.Markdown(visible=False)
        
                
        with gr.Group(visible=False) as results_group:
            
            with gr.Accordion("Open to See the Output Table", open=True) as table_accordion:    
                  output_table = gr.HTML(render=True)
              
            gr.Markdown(" ") # A simple blank markdown can create space
                
            report_button = gr.Button("Report an unsatisfactory output for a free credit.",elem_id="run-btn")
            report_textbox = gr.Textbox(
                label="Describe the issue",
                lines=4,
                placeholder="e.g. DQ981467: it gives me unknown when I can in fact search it on NCBI \n DQ981467: cannot find the result in batch output when the live processing did show already processed",
                visible=False) 
            submit_report_button = gr.Button("Submit", visible=False, elem_id="run-btn")
            status_report = gr.Markdown(visible=False)  
    
            # Use gr.Markdown to add a visual space
            gr.Markdown(" ") # A simple blank markdown can create space
    
            download_file = gr.File(label="Download File Here", visible=False, interactive=True)
            
            gr.Markdown(" ") # A simple blank markdown can create space
    
            
            with gr.Group(visible=True, elem_id="nps-overlay") as nps_modal:
                with gr.Group(elem_id="nps-container"):
                    gr.Markdown("### How likely are you to recommend this tool to a colleague or peer?")
            
                    # Score options (0-10)
                    nps_radio = gr.Radio(
                        choices=[str(i) for i in range(11)],
                        label="Select score:",
                        interactive=True,
                        container=False,
                        elem_id="nps-radio-container"
                    )
            
                    # Row for labels under the ends
                    with gr.Row():
                        with gr.Column(scale=1):
                            gr.Markdown("Not likely")
                        with gr.Column(scale=8):
                            gr.Markdown("")  # spacer
                        with gr.Column(scale=1):
                            gr.Markdown("Extremely likely")
            
                    nps_submit = gr.Button("Submit", elem_id="nps-submit-button")
                    nps_output = gr.Textbox(label="", interactive=False, visible=True)

            gr.Markdown(" ") # A simple blank markdown can create space
            
            progress_box = gr.Textbox(label="Live Processing Log", lines=20, interactive=False)   
    
            gr.Markdown("---")
           
        def classify_with_loading():
            return gr.update(value="⏳ Please wait... processing...",visible=True)  # Show processing message
             
        
        active_processes = []
        def stop_batch():
          global_stop_flag.value = True
          return gr.update(value="❌ Stopping...", visible=True)
    
        
        def submit_nps(email,nps_score):
            if nps_score is None:
                return "❌ Please select a score before submitting."
            log_submission_to_gsheet(email, [], nps_score)
            return "✅ Thanks for submitting your feedback!"
                    
        def log_submission_to_gsheet(email, samples, nps_score=None):
            from datetime import datetime, timezone
            import json, os, gspread
            from oauth2client.service_account import ServiceAccountCredentials
            import uuid
        
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if not email.strip():
                email = f"anonymous_{str(uuid.uuid4())[:8]}"
        
            try:
                creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
                scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
                client = gspread.authorize(creds)
        
                sheet = client.open("user_usage_log")
                worksheet = sheet.sheet1  # Main sheet
        
                data = worksheet.get_all_values()
                headers = data[0]
                email_col = headers.index("email")
                samples_col = headers.index("samples")
                recent_time_col = headers.index("recent_time")
                nps_col = headers.index("nps_score") if "nps_score" in headers else -1
                print("this is nps col: ", nps_col)
                # Step 1: Find row matching the email
                for i, row in enumerate(data[1:], start=2):  # start=2 for correct row indexing
                    if row[email_col].strip().lower() == email.strip().lower():
                        old_samples = row[samples_col].strip() if len(row) > samples_col else ""
                        old_sample_list = [s.strip() for s in old_samples.split(",") if s.strip()]
                        all_samples = list(dict.fromkeys(old_sample_list + samples))  # deduplicate while preserving order
                        new_sample_string = ", ".join(all_samples)

                        # Update recent_time to store history
                        old_timestamp = row[recent_time_col].strip() if len(row) > recent_time_col else ""
                        if old_timestamp:
                            new_timestamp = f"{old_timestamp}, {timestamp}"
                        else:
                            new_timestamp = timestamp
                            
                        worksheet.update_cell(i, samples_col + 1, new_sample_string)
                        worksheet.update_cell(i, recent_time_col + 1, str(new_timestamp))
                        if nps_score is not None:
                            print("this is nps score:", nps_score)
                            old_nps = row[nps_col].strip() if len(row) > nps_col else ""
                            if old_nps:
                                new_nps = f"{old_nps},{nps_score}"
                            else:
                                new_nps = str(nps_score)
                            worksheet.update_cell(i, nps_col + 1, str(new_nps))

                        print(f"✅ Updated existing user row for: {email}")
                        return
        
                # Step 2: If email not found, add new row
                new_row = [""] * len(headers)
                new_row[email_col] = email
                new_row[samples_col] = ", ".join(samples)
                new_row[recent_time_col] = timestamp
                if nps_col != -1:
                    if len(new_row) <= nps_col:
                        new_row.extend([""] * (nps_col + 1 - len(new_row)))
                    new_row[nps_col] = str(nps_score) if nps_score is not None else ""
                worksheet.append_row(new_row)
                print(f"✅ Appended new user row for: {email}")
        
            except Exception as e:
                print(f"❌ Failed to log submission to Google Sheets: {e}")


        import multiprocessing
        import time
        
        def run_with_timeout(func, args=(), kwargs={}, timeout=30, stop_value=None):
            """
            Runs func in a separate process with optional timeout.
            If stop_value is provided and becomes True during execution, the process is killed early.
            """
            def wrapper(q, *args, **kwargs):
                try:
                    result = func(*args, **kwargs)
                    q.put((True, result))
                except Exception as e:
                    q.put((False, e))
        
            q = multiprocessing.Queue()
            p = multiprocessing.Process(target=wrapper, args=(q, *args), kwargs=kwargs)
            active_processes.append(p)   # ✅ track it
            p.start()
        
            start_time = time.time()
            while p.is_alive():
                # Timeout check
                if timeout is not None and (time.time() - start_time) > timeout:
                    p.terminate()
                    p.join()
                    print(f"⏱️ Timeout exceeded ({timeout} sec) — function killed.")
                    return False, None
        
                if stop_value is not None and stop_value.value:
                    print("🛑 Stop flag detected — waiting for child to exit gracefully.")
                    p.join(timeout=3)  # short wait for graceful exit
                    if p.is_alive():
                        print("⚠️ Child still alive, forcing termination.")
                        p.terminate()
                        p.join(timeout=2)
                    return False, None
                time.sleep(0.1)  # avoid busy waiting
        
            # Process finished naturally
            if not q.empty():
                success, result = q.get()
                if success:
                    return True, result
                else:
                    raise result
        
            return False, None
        def cleanup_processes():
            global active_processes
            print("inside cleanup process and number of active process: ", len(active_processes))
            for p in active_processes:
                if p.is_alive():
                    try:
                        p.terminate()
                        p.join(timeout=2)
                    except Exception:
                        pass
            active_processes = []

        def summarize_results_sync(acc, stop_flag=None, niche_cases=None):
            print("in sum_resukt_sync and niche case is: ", niche_cases)
            return asyncio.run(mtdna_backend.summarize_results(acc, stop_flag, niche_cases))
            
        def threaded_batch_runner(file=None, text="", email="", niche_cases=None):
            print("clean everything remain before running")
            cleanup_processes()
            print("📧 EMAIL RECEIVED:", repr(email))
            import tempfile
            from mtdna_backend import (
                extract_accessions_from_input,
                summarize_results,
                save_to_excel,
                increment_usage,
            )
            import os
                
            global_stop_flag.value = False  # reset stop flag
            #active_processes = []
            
            tmp_dir = tempfile.mkdtemp()
            output_file_path = os.path.join(tmp_dir, "batch_output_live.xlsx")
            #output_file_path = "/mnt/data/batch_output_live.xlsx"
            all_rows = []
            processed_accessions = 0  # ✅ track successful accessions
            email_tracked = False
            log_lines = []
            usage_text = ""
            processed_info = ""
            if not email.strip():
                output_file_path = None#"Write your email so that you can download the outputs."
                log_lines.append("📥 Provide your email to receive a downloadable Excel report and get 20 more free queries.")
                limited_acc = 30
            if email.strip():
                usage_count, max_allowed = increment_usage(email, processed_accessions) 
                if int(usage_count) >= int(max_allowed):
                    log_lines.append("❌ You have reached your quota. Please contact us to unlock more.")
                    
                    # Minimal blank yield to trigger UI rendering
                    yield (
                        make_html_table([]),            # 1 output_table
                        gr.update(visible=True),        # 2 results_group
                        gr.update(visible=False),       # 3 download_file
                        gr.update(value="", visible=True), # 4 usage_display
                        "⛔️ Quota limit",               # 5 status
                        "⛔️ Quota limit",               # 6 progress_box
                        gr.update(visible=True),        # 7 run_button
                        gr.update(visible=False),       # 8 stop_button
                        gr.update(visible=True),        # 9 reset_button
                        gr.update(visible=True),        # 10 raw_text
                        gr.update(visible=True),        # 11 file_upload
                        gr.update(value=processed_info, visible=False), # 12 processed_info
                        gr.update(visible=False)        # 13 nps_modal
                    )
            
                    # Actual warning frame
                    yield (
                        make_html_table([]),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(value="❌ You have reached your quota. Please contact us to unlock more.", visible=True),
                        "❌ Quota Exceeded",
                        "\n".join(log_lines),
                        gr.update(visible=True),
                        gr.update(visible=False),
                        gr.update(visible=True),
                        gr.update(visible=True),
                        gr.update(visible=True),
                        gr.update(value="", visible=False),
                        gr.update(visible=False)
                    )
                    return
                limited_acc = int(max_allowed-usage_count)
            
            # Step 1: Parse input
            accessions, invalid_accessions, error = extract_accessions_from_input(file, text)

            # Step 1b: Resolve NCBI identifiers — expand BioProjects into
            # BioSamples, map SRR/SAMN back to GenBank accessions.
            # This runs only when there are valid accessions and no hard error.
            if accessions and not error:
                try:
                    from input_handler import build_pipeline_input, get_pipeline_accession
                    yield (
                        make_html_table(all_rows),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        "",
                        "🔍 Resolving accessions...",
                        "Resolving accessions — querying NCBI...",
                        gr.update(visible=False),
                        gr.update(visible=True),
                        gr.update(visible=True),
                        gr.update(visible=True),
                        gr.update(visible=True),
                        gr.update(value=processed_info, visible=True),
                        gr.update(visible=False),
                    )
                    raw_joined = ", ".join(accessions)
                    resolved_dict, skipped_msgs = build_pipeline_input(raw_joined)

                    if skipped_msgs:
                        for msg in skipped_msgs:
                            log_lines.append(f"⚠️ {msg}")
                        invalid_accessions = list(invalid_accessions or []) + skipped_msgs

                    if resolved_dict:
                        # Build ordered list of pipeline accessions from resolved entries
                        pipeline_accs = []
                        for samn_key, entry in resolved_dict.items():
                            pa = get_pipeline_accession(entry, samn_key)
                            if pa and pa not in pipeline_accs:
                                pipeline_accs.append(pa)
                        if pipeline_accs:
                            accessions = pipeline_accs
                            total = len(accessions)
                            log_lines.append(
                                f"✅ Resolved to {total} sample(s). Running pipeline..."
                            )
                    elif not resolved_dict and not any(
                        a for a in (accessions or [])
                    ):
                        error = "Could not resolve any accessions. Please check your input."
                except Exception as _resolve_err:
                    # Resolution failure is non-fatal — fall back to original list
                    log_lines.append(
                        f"⚠️ NCBI resolution step failed ({_resolve_err}); "
                        f"proceeding with original accessions."
                    )
            total = len(accessions)
            print("total len original accessions: ", total)
            if total > 0:
                if total > limited_acc:
                    accessions = accessions[:limited_acc]
                    if invalid_accessions:
                        warning = f"⚠️ Only processing first {limited_acc} accessions. ⚠️ Invalid accessions: {', '.join(invalid_accessions)}."
    
                    else:
                        warning = f"⚠️ Only processing first {limited_acc} accessions."
                else:
                    if invalid_accessions:
                        warning = f"✅ All {total} accessions will be processed. ⚠️ Invalid accessions: {', '.join(invalid_accessions)}."
                    else:
                        warning = f"✅ All {total} accessions will be processed."
            else:
                if invalid_accessions:
                    warning = f"⚠️ Invalid accessions: {', '.join(invalid_accessions)}."
                else:
                    warning = "Nothing to processing"
            if len(accessions) == 1:    
                processed_info = warning + "\n" +f"Processed accessions: {accessions[0]}"
            else:    
                if len(accessions) > 0:
                    processed_info = warning + "\n" +f"Processed accessions: {accessions[0]}...{accessions[-1]}"   
                elif len(accessions) == 0:
                    processed_info = warning
                else:
                    processed_info = "⚠️ Cannot process the input"
            ### NEW: Hide inputs, show processed_info at start
            yield (
                make_html_table(all_rows), # output_table
                gr.update(visible=False),   # results_group
                gr.update(visible=False),   # download_file
                "", # usage_display
                "⏳ Processing...", # status
                "", # progess_box
                gr.update(visible=False), # run_button,
                gr.update(visible=True),     # show stop button
                gr.update(visible=True),     # show reset button
                gr.update(visible=True),    # hide raw_text
                gr.update(visible=True),    # hide file_upload
                gr.update(value=processed_info, visible=True),  # processed_info
                gr.update(visible=False)     # hide NPS modal at start
            )
            
            log_submission_to_gsheet(email, accessions)

            print("🧪 Accessions received:", accessions)
            if error:
                yield (
                    "",                               # 1 output_table
                    gr.update(visible=False),         # 2 results_group
                    gr.update(visible=False),         # 3 download_file
                    "",                               # 4 usage_display
                    "❌ Error",                        # 5 status
                    str(error),                       # 6 progress_box
                    gr.update(visible=True),          # 7 run_button
                    gr.update(visible=False),         # 8 stop_button
                    gr.update(visible=True),          # 9 reset_button
                    gr.update(visible=True),          # 10 raw_text
                    gr.update(visible=True),          # 11 file_upload
                    gr.update(value="", visible=False), # 12 processed_info
                    gr.update(visible=False)           # 13 nps_modal
                )
                return
        
            
            if niche_cases and niche_cases.strip():
                niche_cases_list = [x.strip() for x in niche_cases.split(",") if x.strip()]
            else:
                niche_cases_list = None
                # print("this is niche case in the None: ", niche_cases_list)
                # niche_cases_list = ["ethnicity"]
            print("niche case is: ", niche_cases_list)    
            for i, acc in enumerate(accessions):
                try:
                    if global_stop_flag.value:
                        log_lines.append(f"🛑 Stopped at {acc} ({i+1}/{total})")
                        usage_text = ""
            
                        if email.strip() and not email_tracked:
                            print(f"🧪 increment_usage at STOP: {email=} {processed_accessions=}")
                            usage_count, max_allowed = increment_usage(email, processed_accessions)
                            email_tracked = True
                            usage_text = f"**{usage_count}**/{max_allowed} allowed samples used by this email."
                            #Ten more samples are added first (you now have 60 limited accessions), then wait we will contact you via this email."
                        else:
                            usage_text = f"The limited accession is 30. The user has used {processed_accessions}, and only {30 - processed_accessions} left."
            
                        
                        cleanup_processes()   # ✅ hard kill anything left
                        yield (
                            make_html_table(all_rows),
                            gr.update(visible=True),                           # results_group
                            gr.update(value=output_file_path, visible=bool(output_file_path)),  # download_file
                            gr.update(value=usage_text, visible=True),         # usage_display
                            "🛑 Stopped",                                       # "✅ Done" or "🛑 Stopped"
                            "\n".join(log_lines),
                            gr.update(visible=False),                          # run_button
                            gr.update(visible=False),                          # stop_button
                            gr.update(visible=True),                           # reset_button
                            gr.update(visible=True),                          # raw_text
                            gr.update(visible=True),                          # file_upload
                            gr.update(value=processed_info, visible=False),                # processed_info
                            gr.update(visible=True)                            # NPS modal now visible
                        )
    
                        return
            
                    log_lines.append(f"Running pipeline on {total} sample(s)... [{i+1}/{total}] Processing {acc}")
                    
                    # Hide inputs, show processed_info at start
                    yield (
                        make_html_table(all_rows),         # output_table
                        gr.update(visible=True),          # results_group
                        gr.update(visible=False),          # download_file
                        "",                                # usage_display
                        "⏳ Processing...",                 # status
                        "\n".join(log_lines),              # progress_box
                        gr.update(visible=False),           # run_button
                        gr.update(visible=True),           # stop_button
                        gr.update(visible=True),           # reset_button
                        gr.update(visible=True),          # hide raw_text
                        gr.update(visible=True),          # hide file_upload
                        gr.update(value=processed_info, visible=True),  # processed_info
                        gr.update(visible=False)           # hide NPS modal at start
                    )

                    print("📄 Processing accession:", acc)
                    # --- Before calling summarize_results ---
                    samples_left = total - i  # including current one
                    estimated_seconds_left = samples_left * 100  # your observed average per sample
                    
                    log_lines.append(
                        f"Running... usually ~100s per sample"
                    )
                    log_lines.append(
                        f"⏳ Estimated time left: ~{estimated_seconds_left} seconds ({samples_left} sample{'s' if samples_left > 1 else ''} remaining)"
                    )
                    
                    # Yield update to UI before the heavy pipeline call
                    yield (
                        make_html_table(all_rows),
                        gr.update(visible=True),          # results_group
                        gr.update(visible=False),         # download_file
                        "",                               # usage_display
                        "⏳ Processing...",                # status
                        "\n".join(log_lines),             # progress_box
                        gr.update(visible=False),         # run_button
                        gr.update(visible=True),          # stop_button
                        gr.update(visible=True),          # reset_button
                        gr.update(visible=True),         # raw_text
                        gr.update(visible=True),         # file_upload
                        gr.update(value=processed_info, visible=True),  # processed_info
                        gr.update(visible=False)          # hide NPS modal
                    )
    
                    # Run summarize_results in a separate process with stop flag support
                    success, rows = run_with_timeout(
                        #summarize_results,
                        summarize_results_sync,
                        args=(acc,global_stop_flag, niche_cases_list),
                        timeout=None,              # or set max seconds per sample if you want
                        stop_value=global_stop_flag
                    )
                    
                    # If stop was pressed during this accession
                    if not success and global_stop_flag.value:
                        log_lines.append(f"🛑 Cancelled {acc} before completion")
                        
                        cleanup_processes()   # ✅ hard kill anything left
                        yield (
                            make_html_table(all_rows),
                            gr.update(visible=True),                           # results_group
                            gr.update(value=output_file_path, visible=bool(output_file_path)),  # download_file
                            gr.update(value=usage_text, visible=True),         # usage_display
                            "🛑 Stopped",                                       # "✅ Done" or "🛑 Stopped"
                            "\n".join(log_lines),
                            gr.update(visible=False),                          # run_button
                            gr.update(visible=False),                          # stop_button
                            gr.update(visible=True),                           # reset_button
                            gr.update(visible=True),                          # raw_text
                            gr.update(visible=True),                          # file_upload
                            gr.update(value="", visible=False),                # processed_info
                            gr.update(visible=True)                            # NPS modal now visible
                        )
    
                        break  # stop processing entirely
                    
                    # If it finished normally
                    if success and rows:
                        all_rows.extend(rows)
                        processed_accessions += 1
                        if email.strip():
                            save_to_excel(all_rows, "", "", output_file_path, is_resume=False)
                        log_lines.append(f"✅ Processed {acc} ({i+1}/{total})")
                    else:
                        # If it failed due to timeout or other error
                        if not global_stop_flag.value:
                            log_lines.append(f"⚠️ Skipped {acc} due to timeout or error")
                    
                    # Always yield updated logs after each attempt
                    
                    yield (
                        make_html_table(all_rows),         # output_table
                        gr.update(visible=True),          # results_group
                        gr.update(visible=False),          # download_file
                        "",                                # usage_display
                        "⏳ Processing...",                 # status
                        "\n".join(log_lines),              # progress_box
                        gr.update(visible=False),           # run_button
                        gr.update(visible=True),           # stop_button
                        gr.update(visible=True),           # reset_button
                        gr.update(visible=True),          # hide raw_text
                        gr.update(visible=True),          # hide file_upload
                        gr.update(value=processed_info, visible=True),  # processed_info
                        gr.update(visible=False)           # hide NPS modal at start
                    )
            

                except Exception as e:
                    log_lines.append(f"❌ Failed to process {acc}: {e}. Report on the box above so that we won't count this bad one for you (email required).")
                    yield (
                        make_html_table(all_rows),         # output_table
                        gr.update(visible=True),          # results_group
                        gr.update(visible=False),          # download_file
                        "",                                # usage_display
                        "⏳ Processing...",                 # status
                        "\n".join(log_lines),              # progress_box
                        gr.update(visible=False),           # run_button
                        gr.update(visible=True),           # stop_button
                        gr.update(visible=True),           # reset_button
                        gr.update(visible=True),          # hide raw_text
                        gr.update(visible=True),          # hide file_upload
                        gr.update(value=processed_info, visible=True),  # processed_info
                        gr.update(visible=False)           # hide NPS modal at start
                    )
                
            # Step 3: Final usage update
            usage_text = ""
            if email.strip() and not email_tracked:
                print(f"🧪 increment_usage at END: {email=} {processed_accessions=}")
                usage_count, max_allowed = increment_usage(email, processed_accessions)
                email_tracked = True
                usage_text = f"**{usage_count}**/{max_allowed} allowed samples used by this email." 
                #Ten more samples are added first (you now have 60 limited accessions), then wait we will contact you via this email."
            elif not email.strip():
                usage_text = f"The limited accession is 30. The user has used {processed_accessions}, and only {30 - processed_accessions} left."
        
            yield (
                make_html_table(all_rows),
                gr.update(visible=True),                           # results_group
                gr.update(value=output_file_path, visible=bool(output_file_path)),  # download_file
                gr.update(value=usage_text, visible=True),         # usage_display
                "✅ Done",                                      # "✅ Done" or "🛑 Stopped"
                "\n".join(log_lines),
                gr.update(visible=False),                          # run_button
                gr.update(visible=False),                          # stop_button
                gr.update(visible=True),                           # reset_button
                gr.update(visible=True),                          # raw_text
                gr.update(visible=True),                          # file_upload
                gr.update(value=processed_info, visible=True),                # processed_info
                gr.update(visible=True)                            # NPS modal now visible
            )
    
        # SUBMIT REPORT UI
        # 1. Google Sheets setup
        def get_worksheet(sheet_name="Report"):
            import os, json
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            try:
                creds_dict = json.loads(os.environ["GCP_CREDS_JSON"])
                scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
                creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
                client = gspread.authorize(creds)
                sheet = client.open(sheet_name).sheet1
                return sheet
            except Exception as e:
                print(f"❌ Error loading Google Sheet '{sheet_name}':", e)
                return None
                
        # 2. Submit function to send report to the Google Sheet
        def submit_report(report_text,user_email=""):
            try:
                sheet = get_worksheet()
                # ✅ Parse the report_text (each line like 'ACCESSION: message')
                lines = report_text.strip().split('\n')
                user = ""
                if user_email.strip():
                    user = user_email
                for line in lines:
                    if ':' in line:
                        accession, message = line.split(':', 1)
                        sheet.append_row([accession.strip(), message.strip(), user.strip()])
                return "✅ Report submitted successfully!"
            except Exception as e:
                return f"❌ Error submitting report: {str(e)}"
        def show_report_ui():
            return gr.update(visible=True), gr.update(visible=True), gr.update(visible=False)

        def handle_submission(text,user_email):
            msg = submit_report(text, user_email)
            return gr.update(value=msg, visible=True), gr.update(visible=False), gr.update(visible=False)        

        def make_html_table(rows, sort_by_score=False, ascending=False):
            """
            Dynamically builds an HTML table based on the detected headers in `rows`.
            Works with both list-of-lists and list-of-dicts.
            Automatically includes any new columns (e.g. niche_cases).
            """
            if not rows:
                return "<p style='color:#ccc;'>No results to display.</p>"
        
            # 🧩 Detect if rows are list-of-lists or list-of-dicts
            if isinstance(rows[0], dict):
                data_dicts = rows
                # dynamic headers from all dict keys (union of keys)
                print("make html table: ", data_dicts)
                all_keys = []
                for r in data_dicts:
                    for k in r.keys():
                        if k not in all_keys:
                            all_keys.append(k)
                headers = ["No."] + all_keys
            else:
                # fallback: static columns (old format)
                headers = [
                    "No.", "Sample ID", "Predicted Country", "Country Explanation",
                    "Predicted Sample Type", "Sample Type Explanation", "Sources", "Time cost", "Confidence Score"
                ]
                data_dicts = [dict(zip(headers[1:], r)) for r in rows]
        
            # 🧱 Build HTML table
            html = """
            <div style='overflow-x: auto; padding: 10px;'>
                <div style='max-height: 400px; overflow-y: auto; border: 1px solid #444; border-radius: 8px;'>
                    <table style='width:100%; border-collapse: collapse; table-layout: auto; font-size: 14px; color: #f1f1f1; background-color: #1e1e1e;'>
                        <thead style='position: sticky; top: 0; background-color: #2c2c2c; z-index: 1;'>
                            <tr>
            """
            # Add headers dynamically
            html += "".join(
                f"<th style='padding:10px; border:1px solid #555; text-align:left; white-space:nowrap;'>{h}</th>"
                for h in headers
            )
            html += "</tr></thead><tbody>"

            # # 🏗️ Convert Confidence Score to an integer value for sorting purposes
            # for idx, row in enumerate(data_dicts):
            #     # Parse the confidence score (f"{tier} ({score})\n{explanations_score}")
            #     conf_score_str = row.get("Confidence Score", "")
            #     score_value = None
        
            #     # Try to extract the score value (assuming it's in parentheses)
            #     if conf_score_str:
            #         try:
            #             score_value = float(conf_score_str.split('(')[1].split(')')[0].strip())
            #             score_value = conf_score_str
            #         except:
            #             score_value = None
        
            #     row['Confidence Score'] = score_value  # Add as a new key for sorting purposes
        
            # 🧹 Sort rows by Confidence Score if needed
            # if sort_by_score and data_dicts:
            #     data_dicts = sorted(data_dicts, key=lambda x: x.get('Confidence Score Value', 0), reverse=not ascending)

            # Fill rows
            for idx, row in enumerate(data_dicts, 1):
                html += "<tr>"
                html += f"<td style='padding:10px; border:1px solid #555;'>{idx}</td>"
                for h in headers[1:]:
                    col = row.get(h, "")
                    style = "padding:10px; border:1px solid #555; vertical-align:top;"
                    if h == "Sources" and isinstance(col, str):
                        links = [
                            f"<a href='{url.strip()}' target='_blank' style='color:#4ea1f3; text-decoration:underline;'>{url.strip()}</a>"
                            for url in col.strip().split("\\n") if url.strip()
                        ]
                        col = "- " + "<br>- ".join(links)
                    elif isinstance(col, str):
                        col = col.replace("\\n", "<br>")
                        col = col.replace("\n", "<br>")
                    html += f"<td style='{style}'>{col}</td>"
                html += "</tr>"
        
            html += "</tbody></table></div></div>"
            return html

        def reset_fields():
            global_stop_flag.value = True  # Stop any running job
            cleanup_processes()   # ✅ same cleanup here

            return (
                gr.update(value="", visible=True),   # raw_text
                gr.update(value=None, visible=True), # file_upload
                gr.update(value=[], visible=True),   # output_table
                gr.update(value="", visible=True),   # status
                gr.update(visible=False),            # results_group
                gr.update(value="", visible=True),   # usage_display
                gr.update(value="", visible=True),   # progress_box
                gr.update(value="", visible=False),  # report_textbox
                gr.update(visible=False),             # submit_report_button
                gr.update(value="", visible=False),  # status_report
                gr.update(value="", visible=False),  # processed_info
                gr.update(visible=False),              # hide NPS modal
                gr.update(visible=True),              # run_button ✅ restore
                gr.update(visible=False)              # stop button 
            )

        interface.queue()  # No arguments here!
        
        run_button.click(
            fn=threaded_batch_runner,
            inputs=[file_upload, raw_text, user_email, niche_input],
            outputs=[
                output_table,      # 1
                results_group,     # 2
                download_file,     # 3
                usage_display,     # 4
                status,            # 5
                progress_box,      # 6
                run_button,        # 7
                stop_button,       # 8
                reset_button,      # 9
                raw_text,          # 10
                file_upload,       # 11
                processed_info,    # 12
                nps_modal          # 13
            ],
            concurrency_limit=1,
            queue=True
        )

        
    
        
    
        stop_button.click(fn=stop_batch, inputs=[], outputs=[status])
    
        reset_button.click(
            fn=reset_fields,
            inputs=[],
            outputs=[
                raw_text, 
                file_upload, 
                output_table, 
                status, 
                results_group, 
                usage_display, 
                progress_box,
                report_textbox,
                submit_report_button,
                status_report, 
                processed_info,
                nps_modal,
                run_button,
                stop_button
            ]
        )

        report_button.click(fn=show_report_ui, outputs=[report_textbox, submit_report_button, status_report])
        submit_report_button.click(fn=handle_submission, inputs=[report_textbox, user_email], outputs=[status_report, report_textbox, submit_report_button])
        
        nps_submit.click(fn=submit_nps, inputs=[user_email, nps_radio], outputs=[nps_output])
        # Link each button to submit function
        
        gr.HTML("""
        <style>
          body, html {
              background-color: #121212 !important;
              color: #ffffff !important;
          }
        
          .gradio-container, .gr-block, .gr-box, textarea, input, select, .prose, .prose * {
              background-color: #1e1e1e !important;
              color: #ffffff !important;
              border-color: #333 !important;
          }
        
          textarea::placeholder,
          input::placeholder {
              color: #aaa !important;
          }
        
          button {
              background-color: #2d2d2d !important;
              color: #fff !important;
              border: 1px solid #444 !important;
          }
        
          a {
              color: #4ea1f3 !important;
          }

          /* Shared hover style for the three main buttons */
          #run-btn:hover, #stop-btn:hover, #reset-btn:hover {
              border-color: white !important;
              box-shadow: 0 0 5px white;
              transition: border-color 0.2s ease-in-out, box-shadow 0.2s ease-in-out;
          }
        
          /* Active click style */
          #run-btn:active, #stop-btn:active, #reset-btn:active {
              border-color: white !important;
              box-shadow: 0 0 5px white inset;
              }

        </style>
        """)
    
    with gr.Tab("Curious about this product?"):
        gr.HTML(value=flow_chart)
    
    with gr.Tab("Pricing"):
        gr.HTML(value=pricing_html)

interface.launch(share=True,debug=True)