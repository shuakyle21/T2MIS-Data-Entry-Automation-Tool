"""Browser factory. Selenium Manager resolves chromedriver automatically."""

from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait


def create_driver(wait_timeout=20):
    """Create a maximized Chrome driver and return (driver, wait)."""
    driver = webdriver.Chrome()
    driver.maximize_window()
    wait = WebDriverWait(driver, wait_timeout, poll_frequency=0.1)
    return driver, wait
