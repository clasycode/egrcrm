"""Windowed entry point with a visible diagnostic if the control panel cannot start."""
if __name__ == '__main__':
    try:
        from desktop_app.gui import main
        main()
    except Exception:
        import os
        import traceback
        text='Не удалось открыть окно CRM. Проверьте Python 3.12 с компонентом Tcl/Tk.'
        try:
            from desktop_app.storage import default_data_dir
            logs=default_data_dir()/'logs';logs.mkdir(parents=True,exist_ok=True)
            path=logs/'startup-error.log';path.write_text(traceback.format_exc(),encoding='utf-8')
            text+='\nПодробности: '+str(path)
        except Exception:pass
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,text,'RudyCortex — ошибка запуска',0x10)
        else:raise
