#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
番茄小说自动上传脚本
将novel-ai生成的txt章节自动上传到番茄小说作家后台
"""

import os
import time
import argparse
import re
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

class FanqieUploader:
    def __init__(self, username, password, novel_name, chapter_files, headless=False):
        """
        初始化上传器
        :param username: 番茄小说作家账号
        :param password: 番茄小说作家密码
        :param novel_name: 小说名称
        :param chapter_files: 章节文件列表
        :param headless: 是否无头模式运行
        """
        self.username = username
        self.password = password
        self.novel_name = novel_name
        self.chapter_files = chapter_files
        self.headless = headless
        self.driver = self._init_driver()
        self.base_url = "https://fanqienovel.com"
    
    def _init_driver(self):
        """
        初始化浏览器驱动
        """
        chrome_options = Options()
        if self.headless:
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
        
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_window_size(1920, 1080)
        return driver
    
    def login(self):
        """
        登录番茄小说作家后台
        """
        print("正在登录番茄小说作家后台...")
        login_url = f"{self.base_url}/login"
        self.driver.get(login_url)
        
        try:
            # 等待登录页面加载
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            
            # 输入用户名和密码
            username_input = self.driver.find_element(By.NAME, "username")
            password_input = self.driver.find_element(By.NAME, "password")
            
            username_input.send_keys(self.username)
            password_input.send_keys(self.password)
            password_input.send_keys(Keys.ENTER)
            
            # 等待登录成功
            WebDriverWait(self.driver, 15).until(
                EC.url_contains("/writer")
            )
            print("登录成功！")
            return True
        except Exception as e:
            print(f"登录失败: {e}")
            return False
    
    def navigate_to_novel(self):
        """
        导航到指定小说的管理页面
        """
        print(f"正在查找小说: {self.novel_name}")
        
        # 进入作品管理页面
        works_url = f"{self.base_url}/writer/works"
        self.driver.get(works_url)
        
        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "work-list"))
            )
            
            # 查找小说
            work_items = self.driver.find_elements(By.CLASS_NAME, "work-item")
            target_work = None
            
            for item in work_items:
                try:
                    title = item.find_element(By.CLASS_NAME, "work-title").text
                    if self.novel_name in title:
                        target_work = item
                        break
                except Exception:
                    continue
            
            if not target_work:
                print(f"未找到小说: {self.novel_name}")
                return False
            
            # 进入小说管理页面
            manage_link = target_work.find_element(By.CSS_SELECTOR, "a[href*='/writer/work/']")
            manage_link.click()
            
            WebDriverWait(self.driver, 10).until(
                EC.url_contains("/writer/work/")
            )
            print(f"成功进入小说管理页面: {self.novel_name}")
            return True
        except Exception as e:
            print(f"导航到小说页面失败: {e}")
            return False
    
    def get_current_chapter_count(self):
        """
        获取番茄小说后台当前章节数
        """
        print("正在获取当前章节数...")
        
        try:
            # 进入章节管理页面
            chapter_tab = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), '章节管理')]"))
            )
            chapter_tab.click()
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "chapter-list"))
            )
            
            # 查找章节列表中的章节数
            chapter_items = self.driver.find_elements(By.CLASS_NAME, "chapter-item")
            chapter_count = len(chapter_items)
            
            print(f"当前小说已有 {chapter_count} 章")
            return chapter_count
        except Exception as e:
            print(f"获取章节数失败: {e}")
            return 0
    
    def upload_chapter(self, chapter_file):
        """
        上传单个章节
        :param chapter_file: 章节文件路径
        """
        print(f"正在上传章节: {chapter_file}")
        
        try:
            # 点击"创建章节"按钮
            create_chapter_btn = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), '创建章节')]"))
            )
            create_chapter_btn.click()
            
            # 等待编辑器加载
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CLASS_NAME, "chapter-editor"))
            )
            
            # 提取章节标题和内容
            chapter_title, chapter_content = self._parse_chapter_file(chapter_file)
            
            # 输入章节标题
            title_input = self.driver.find_element(By.NAME, "chapter_title")
            title_input.clear()
            title_input.send_keys(chapter_title)
            
            # 输入章节内容
            # 注意：这里需要根据番茄小说的编辑器实际情况调整
            # 可能需要使用iframe或者其他方式定位编辑器
            content_editor = self.driver.find_element(By.CLASS_NAME, "editor-content")
            content_editor.clear()
            content_editor.send_keys(chapter_content)
            
            # 点击发布按钮
            publish_btn = self.driver.find_element(By.XPATH, "//button[contains(text(), '发布')]")
            publish_btn.click()
            
            # 等待发布成功
            WebDriverWait(self.driver, 15).until(
                EC.alert_is_present()
            )
            
            # 处理成功提示
            alert = self.driver.switch_to.alert
            alert.accept()
            
            print(f"章节上传成功: {chapter_title}")
            return True
        except Exception as e:
            print(f"上传章节失败: {e}")
            return False
    
    def _parse_chapter_file(self, chapter_file):
        """
        解析章节文件，提取标题和内容
        :param chapter_file: 章节文件路径
        :return: (标题, 内容)
        """
        with open(chapter_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 提取标题（假设第一行为标题）
        lines = content.split('\n')
        title = lines[0].strip() if lines else f"第{os.path.basename(chapter_file).split('章')[0]}章"
        
        # 提取内容（去除标题行）
        chapter_content = '\n'.join(lines[1:]) if len(lines) > 1 else content
        
        return title, chapter_content
    
    def run(self):
        """
        执行上传流程
        """
        try:
            if not self.login():
                return False
            
            if not self.navigate_to_novel():
                return False
            
            # 获取当前章节数
            current_chapter_count = self.get_current_chapter_count()
            print(f"番茄小说后台当前章节数: {current_chapter_count}")
            
            # 筛选需要上传的章节
            chapters_to_upload = []
            for chapter_file in self.chapter_files:
                # 从文件名中提取章节号
                filename = os.path.basename(chapter_file)
                chapter_num_match = re.search(r'第(\d+)章', filename)
                if chapter_num_match:
                    chapter_num = int(chapter_num_match.group(1))
                    if chapter_num > current_chapter_count:
                        chapters_to_upload.append((chapter_num, chapter_file))
            
            # 按章节号排序
            chapters_to_upload.sort(key=lambda x: x[0])
            chapters_to_upload = [file for _, file in chapters_to_upload]
            
            if not chapters_to_upload:
                print("没有需要上传的新章节")
                return True
            
            print(f"找到 {len(chapters_to_upload)} 个需要上传的新章节")
            for file in chapters_to_upload:
                print(f"  - {os.path.basename(file)}")
            
            # 上传新章节
            for chapter_file in chapters_to_upload:
                if not self.upload_chapter(chapter_file):
                    print(f"上传失败，继续处理下一个章节")
                # 等待一段时间，避免触发反爬
                time.sleep(5)
            
            print("所有新章节上传完成！")
            return True
        finally:
            self.driver.quit()

def main():
    parser = argparse.ArgumentParser(description="番茄小说自动上传脚本")
    parser.add_argument('--username', required=True, help='番茄小说作家账号')
    parser.add_argument('--password', required=True, help='番茄小说作家密码')
    parser.add_argument('--novel', required=True, help='小说名称')
    parser.add_argument('--chapter-dir', required=True, help='章节文件目录')
    parser.add_argument('--headless', action='store_true', help='无头模式运行')
    
    args = parser.parse_args()
    
    # 收集章节文件
    chapter_files = []
    chapter_dir = Path(args.chapter_dir)
    
    if chapter_dir.exists() and chapter_dir.is_dir():
        for file in sorted(chapter_dir.glob('*.txt')):
            chapter_files.append(str(file))
    else:
        print(f"章节目录不存在: {args.chapter_dir}")
        return
    
    if not chapter_files:
        print(f"章节目录中没有txt文件: {args.chapter_dir}")
        return
    
    print(f"找到 {len(chapter_files)} 个章节文件")
    for file in chapter_files:
        print(f"  - {os.path.basename(file)}")
    
    # 初始化上传器
    uploader = FanqieUploader(
        username=args.username,
        password=args.password,
        novel_name=args.novel,
        chapter_files=chapter_files,
        headless=args.headless
    )
    
    # 执行上传
    uploader.run()

if __name__ == "__main__":
    main()
