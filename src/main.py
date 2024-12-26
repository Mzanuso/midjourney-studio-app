import sys
import os
import logging
from datetime import datetime
from PyQt5.QtWidgets import QApplication
from ui.main_window import MidjourneyStudioApp

def setup_logging():
    """Configure logging system"""
    log_dir = os.path.join("D:", "AI_Art_Studio", "system", "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f"studio_{datetime.now().strftime('%Y%m%d')}.log")
    
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logging.info("Logging system initialized")

def setup_environment():
    """Setup base environment and directories"""
    base_dir = "D:\\AI_Art_Studio"
    directories = [
        os.path.join(base_dir, "midjourney_output"),
        os.path.join(base_dir, "midjourney_output", "01_ANALYSIS"),
        os.path.join(base_dir, "midjourney_output", "CARD"),
        os.path.join(base_dir, "system", "logs"),
        os.path.join(base_dir, "system", "temp")
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        logging.debug(f"Directory verified: {directory}")

def main():
    """Main application entry point"""
    # Setup environment
    setup_logging()
    setup_environment()
    
    # Initialize application
    app = QApplication(sys.argv)
    window = MidjourneyStudioApp()
    window.show()
    
    logging.info("Application started")
    
    # Start application loop
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()