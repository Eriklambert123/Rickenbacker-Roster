st.divider()
        st.subheader("💾 System Backups & Disaster Recovery")
        st.markdown("Download a full snapshot of all active tables, or restore the database from a previously downloaded snapshot.")

        bak_col1, bak_col2 = st.columns(2)

        # --- 1. EXPORT SNAPSHOT ---
        with bak_col1:
            st.markdown("#### 📤 Export Backup")
            st.caption("Creates an Excel workbook containing a complete snapshot of every database table.")
            
            if st.button("Generate Backup File", use_container_width=True):
                import io
                
                conn = get_db_connection()
                table_queries = {
                    "Personnel": "SELECT name, seniority, core_manning, schedule, shift FROM Personnel",
                    "Overtime_Buckets": "SELECT name, seniority, shift, current_hours, contact FROM Overtime_Buckets",
                    "Leave_Ledger": "SELECT id, target_date, name, leave_type, start_time, end_time, total_hours FROM Leave_Ledger",
                    "Overtime_Log": "SELECT id, target_date, name, ot_type, start_time, end_time, total_hours FROM Overtime_Log",
                    "Shift_Trades_V3": "SELECT id, date_1, name_1, date_2, name_2, start_time, end_time, total_hours FROM Shift_Trades_V3",
                    "Position_Log": "SELECT id, target_date, name, position, hours FROM Position_Log",
                    "OT_Archive": "SELECT id, archive_stamp, notes, name, start_hours, hours_applied, status FROM OT_Archive",
                    "ARO_Watch": "SELECT id, target_date, watch_period, name FROM ARO_Watch",
                    "Manual_Overrides": "SELECT id, target_date, name, seat FROM Manual_Overrides"
                }
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    for t_name, q in table_queries.items():
                        df = pd.read_sql(q, conn)
                        df.to_excel(writer, sheet_name=t_name, index=False)
                conn.close()
                
                snap_time = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
                st.download_button(
                    label=f"⬇️ Download Snapshot ({snap_time}.xlsx)",
                    data=output.getvalue(),
                    file_name=f"RFD_Roster_Backup_{snap_time}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary",
                    use_container_width=True
                )

        # --- 2. RESTORE SNAPSHOT ---
        with bak_col2:
            st.markdown("#### 📥 Restore from Backup")
            st.caption("Upload a verified snapshot file to completely reset the database to that moment in time.")
            
            uploaded_backup = st.file_uploader("Upload Backup Excel File", type=["xlsx"], key="restore_file")
            
            if uploaded_backup is not None:
                st.warning("⚠️ Restoring will overwrite all current data with the contents of this file.")
                if st.button("🚨 Overwrite & Restore Database", type="primary", use_container_width=True):
                    try:
                        excel_file = pd.ExcelFile(uploaded_backup)
                        required_sheets = [
                            "Personnel", "Overtime_Buckets", "Leave_Ledger", 
                            "Overtime_Log", "Shift_Trades_V3", "Position_Log", 
                            "OT_Archive", "ARO_Watch", "Manual_Overrides"
                        ]
                        
                        missing_sheets = [s for s in required_sheets if s not in excel_file.sheet_names]
                        if missing_sheets:
                            st.error(f"Invalid backup file! Missing sheets: {', '.join(missing_sheets)}")
                        else:
                            conn = get_db_connection()
                            c = conn.cursor()
                            
                            # Execute entire restore inside a single database transaction
                            # 1. Clear existing rows
                            for t in reversed(required_sheets):
                                c.execute(f"DELETE FROM {t};")
                                
                            # 2. Re-populate tables
                            for t in required_sheets:
                                df = pd.read_excel(excel_file, sheet_name=t)
                                df = df.where(pd.notnull(df), None)
                                
                                if not df.empty:
                                    cols = list(df.columns)
                                    col_str = ", ".join(cols)
                                    val_placeholders = ", ".join(["%s"] * len(cols))
                                    query = f"INSERT INTO {t} ({col_str}) VALUES ({val_placeholders})"
                                    
                                    records = [tuple(row) for row in df.to_numpy()]
                                    c.executemany(query, records)
                                    
                                # Reset PostgreSQL auto-increment sequences for ID columns
                                if "id" in [c.lower() for c in df.columns]:
                                    c.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), coalesce(max(id), 1), max(id) IS NOT null) FROM {t};")
                            
                            conn.commit()
                            conn.close()
                            st.success("✅ Database successfully restored to snapshot state!")
                            time.sleep(2)
                            st.rerun()
                    except Exception as e:
                        if 'conn' in locals() and conn:
                            conn.rollback()
                            conn.close()
                        st.error(f"Restore failed: {e}")
