#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
web_check.py - Gemini AI 기반 웹 품질 종합 점검

주요 특징:
- Gemini API로 스크린샷 분석
- AI가 실제 화면을 보고 품질 이슈 판단
- ISO 25010 기반 품질 평가
"""

import os
import sys
import time
import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from collections import deque
from urllib.parse import urlparse, urljoin, urldefrag
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright, Page
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai

# ===== 설정 =====
BASE_URL = "https://www.shinhansec.com"
DELAY = 0.5
TIMEOUT = 30000

# ⚠️ 여기에 Gemini API 키를 입력하세요
GEMINI_API_KEY = "AIzaSyBxYA54DQSPde9UqpOndnGpCyp85XkpvuE"

# ISO 25010 품질 특성 한글 매핑
QUALITY_CHARACTERISTICS_KR = {
    "FUNCTIONAL_SUITABILITY": "기능 적합성",
    "PERFORMANCE_EFFICIENCY": "성능 효율성",
    "COMPATIBILITY": "호환성",
    "USABILITY": "사용성",
    "RELIABILITY": "신뢰성",
    "SECURITY": "보안성",
    "MAINTAINABILITY": "유지보수성",
    "PORTABILITY": "이식성"
}

# Gemini 분석 프롬프트
GEMINI_ANALYSIS_PROMPT = """
당신은 웹 접근성 및 품질 전문가입니다. 제공된 웹페이지 스크린샷을 ISO 25010 품질 표준에 따라 **매우 구체적이고 실용적으로** 분석하세요.

## 분석 원칙
1. **실제 문제만 보고**: 정상적으로 작동하는 기능을 문제로 표시하지 마세요
2. **구체적인 위치**: "상단 메뉴 3번째 항목" 처럼 정확한 위치 명시
3. **실용적인 조치**: 개발자가 바로 실행할 수 있는 구체적인 해결 방법
4. **우선순위**: 심각한 문제부터 보고

## 점검 항목

### 1. 기능 적합성 (Functional Suitability)
- **깨진 링크**: href 없음, 404 에러 예상되는 링크
- **작동하지 않는 버튼**: 클릭 불가능하거나 응답 없는 버튼
- **폼 오류**: 필수 입력 필드 누락, 유효성 검사 오류

### 2. 사용성 (Usability)
- **접근성 문제**:
  * 이미지 alt 텍스트 누락 (특히 의미있는 이미지)
  * 대비 부족 (텍스트가 배경과 구분 안됨)
  * 폰트 크기 너무 작음 (10px 이하)
- **인식성 문제**:
  * H1 제목 누락 또는 중복
  * 빈 제목 태그
  * 의미 없는 텍스트 ("클릭", "더보기"만 있음)
- **레이아웃 문제**:
  * 요소 겹침
  * 잘린 텍스트
  * 정렬 오류
- **오탈자**: 명확한 맞춤법 오류나 띄어쓰기 오류

### 3. 호환성 (Compatibility)
- 수평 스크롤바 발생
- 모바일 반응형 문제
- 화면 밖으로 나간 요소

### 4. 성능 효율성 (Performance Efficiency)
- 과도하게 큰 이미지 (파일 크기 추정)
- 불필요한 빈 공간
- 로딩 중 표시 누락

### 5. 보안성 (Security)
- HTTP 사용 (HTTPS 아님)
- 민감정보 노출

## 출력 형식 (JSON)

**중요**: 각 필드를 다음과 같이 매우 구체적으로 작성하세요:

{
  "issues": [
    {
      "quality_main": "품질주특성 (예: 사용성)",
      "quality_sub": "품질부특성 (예: 접근성)",
      "severity": 중요도(60-100, 100이 가장 심각),
      "title": "한 줄 요약 (예: 상단 배너 이미지 alt 속성 누락)",
      "location": "정확한 위치 (예: 페이지 상단, 메인 배너 영역의 좌측 이미지)",
      "element_type": "요소 타입 (예: img, a, button, div)",
      "problem_detail": "문제 상세 설명 (2-3문장으로 왜 문제인지, 사용자에게 어떤 영향인지)",
      "element_selector": "CSS 선택자 추정 (예: .main-banner img:first-child)",
      "code_example": "예상 코드 (예: <img src='banner.jpg'>)",
      "fix_step1": "조치 1단계 (예: 이미지에 의미를 설명하는 alt 속성 추가)",
      "fix_step2": "조치 2단계 (선택사항, 예: 스크린리더 테스트 수행)",
      "fix_code": "수정 코드 예시 (예: <img src='banner.jpg' alt='신한투자증권 메인 프로모션 배너'>)",
      "priority": "우선순위 (즉시/높음/중간/낮음)"
    }
  ]
}

**반드시 JSON 형식만 출력하세요. 다른 설명이나 마크다운은 포함하지 마세요.**
"""


class WebQualityChecker:
    """Gemini AI 기반 웹 품질 점검"""
    
    def __init__(self, base_url: str, max_pages: int, api_key: str):
        self.base_url = base_url
        self.max_pages = max_pages
        self.visited = set()
        self.queue = deque([base_url])
        
        # Gemini API 설정
        if not api_key or api_key == "YOUR_GEMINI_API_KEY_HERE":
            raise ValueError("⚠️ GEMINI_API_KEY를 설정해주세요!")
        
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')
        
        # 리포트 폴더
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.timestamp = timestamp
        self.report_dir = Path("web_report") / timestamp
        self.report_dir.mkdir(parents=True, exist_ok=True)
        
        # 로그 설정
        log_file = self.report_dir / f"{timestamp}.log"
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s [%(levelname)s] %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # CSV 파일
        csv_filename = f"quality_report_{timestamp}.csv"
        self.csv_file = open(self.report_dir / csv_filename, "w", 
                            newline="", encoding="utf-8-sig")
        self.csv_writer = csv.DictWriter(
            self.csv_file,
            fieldnames=["번호", "URL", "품질주특성", "품질부특성", "중요도", "우선순위",
                       "시간", "문제요약", "위치", "요소타입", "문제상세", 
                       "CSS선택자", "현재코드", "수정1단계", "수정2단계", "수정코드예시", "스크린샷"]
        )
        self.csv_writer.writeheader()
        
        self.issue_count = 0
        self.page_count = 0
    
    def analyze_page_with_gemini(self, screenshot_path: Path, url: str, page: Page) -> List[Dict]:
        """Gemini API로 페이지 스크린샷 분석"""
        try:
            self.logger.info(f"  🤖 Gemini AI 분석 중...")
            
            # 이미지 로드
            img = Image.open(screenshot_path)
            
            # Gemini API 호출
            response = self.model.generate_content([
                GEMINI_ANALYSIS_PROMPT,
                img
            ])
            
            # 응답 파싱
            response_text = response.text.strip()
            
            # JSON 추출 (마크다운 코드 블록 제거)
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()
            
            result = json.loads(response_text)
            issues = result.get("issues", [])
            
            self.logger.info(f"  ✓ Gemini가 {len(issues)}개 이슈 발견")
            
            # 이슈 형식 변환
            formatted_issues = []
            for issue in issues:
                self.issue_count += 1
                formatted_issues.append({
                    "main_char": issue.get("quality_main", "기타"),
                    "sub_char": issue.get("quality_sub", "일반"),
                    "severity": issue.get("severity", 50),
                    "priority": issue.get("priority", "중간"),
                    "title": issue.get("title", ""),
                    "description": issue.get("problem_detail", ""),
                    "location": issue.get("location", "미확인"),
                    "element_type": issue.get("element_type", "미확인"),
                    "selector": issue.get("element_selector", ""),
                    "code": issue.get("code_example", ""),
                    "fix_step1": issue.get("fix_step1", ""),
                    "fix_step2": issue.get("fix_step2", ""),
                    "fix_code": issue.get("fix_code", ""),
                    "issue_type": "AI_DETECTED"
                })
            
            return formatted_issues
            
        except json.JSONDecodeError as e:
            self.logger.error(f"  ❌ Gemini 응답 JSON 파싱 실패: {e}")
            self.logger.error(f"  응답 내용: {response_text[:500]}")
            return []
        except Exception as e:
            self.logger.error(f"  ❌ Gemini API 오류: {e}")
            return []
    
    def draw_issue_markers(self, screenshot_path: Path, issues: List[Dict]) -> None:
        """이슈 위치에 마커 표시"""
        try:
            img = Image.open(screenshot_path)
            draw = ImageDraw.Draw(img)
            
            try:
                font = ImageFont.truetype("malgun.ttf", 18)
            except:
                font = ImageFont.load_default()
            
            # 좌상단에 발견된 이슈 개수 표시
            if issues:
                draw.rectangle([10, 10, 250, 60], fill="red", outline="darkred", width=3)
                draw.text((20, 20), f"⚠️ {len(issues)}개 이슈 발견", fill="white", font=font)
            
            img.save(screenshot_path)
        except Exception as e:
            self.logger.error(f"마커 표시 오류: {e}")
    
    def analyze_page(self, page: Page, url: str) -> List[Dict]:
        """페이지 종합 분석"""
        self.logger.info(f"분석 시작: {url}")
        start_time = time.time()
        
        try:
            page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
            page.wait_for_timeout(2000)  # 추가 대기
            
            load_time = (time.time() - start_time) * 1000
            self.logger.info(f"  로딩 시간: {load_time:.0f}ms")
            
            # 스크린샷 촬영
            screenshot_name = f"{self.page_count:04d}_page.png"
            screenshot_path = self.report_dir / screenshot_name
            page.screenshot(path=str(screenshot_path), full_page=True)
            self.logger.info(f"  📸 스크린샷 저장: {screenshot_name}")
            
            # Gemini AI로 분석
            all_issues = self.analyze_page_with_gemini(screenshot_path, url, page)
            
            # 성능 이슈 추가 (로딩 시간)
            if load_time > 3000:
                self.issue_count += 1
                
                # 로딩 시간별 우선순위 및 상세 분석
                if load_time > 5000:
                    priority = "즉시"
                    detail = f"페이지 로딩 시간이 {load_time:.0f}ms로 매우 느립니다. 사용자의 50% 이상이 이탈할 수 있습니다. 주요 원인으로는 최적화되지 않은 이미지, 과도한 JavaScript, 느린 서버 응답 등이 있을 수 있습니다."
                    fix1 = "1) 크롬 개발자도구(F12) > Network 탭에서 가장 오래 걸리는 리소스 확인"
                    fix2 = "2) 이미지는 WebP 형식으로 변환 및 압축 (TinyPNG 등 사용), 3) JavaScript 파일 번들링 및 지연 로딩 적용, 4) CDN 사용 검토"
                elif load_time > 4000:
                    priority = "높음"
                    detail = f"페이지 로딩 시간이 {load_time:.0f}ms로 느립니다. 구글 페이지스피드 권장 기준(3초)을 초과하여 SEO와 사용자 경험에 부정적 영향을 줍니다."
                    fix1 = "1) 이미지 최적화: 적절한 크기로 리사이징, WebP/AVIF 형식 사용"
                    fix2 = "2) 불필요한 외부 스크립트 제거, 3) 브라우저 캐싱 설정, 4) Gzip/Brotli 압축 활성화"
                else:
                    priority = "중간"
                    detail = f"페이지 로딩 시간이 {load_time:.0f}ms로 권장 기준(3초)을 약간 초과합니다. 추가 최적화를 통해 사용자 경험을 개선할 수 있습니다."
                    fix1 = "1) 이미지 지연 로딩(lazy loading) 적용"
                    fix2 = "2) 중요하지 않은 CSS/JS는 비동기 로딩, 3) 페이지스피드 인사이트(https://pagespeed.web.dev)에서 상세 분석"
                
                all_issues.append({
                    "main_char": "성능 효율성",
                    "sub_char": "시간 행동",
                    "severity": min(100, int(70 + (load_time - 3000) / 100)),
                    "priority": priority,
                    "title": f"페이지 로딩 시간 {load_time:.0f}ms (권장: 3초 이하)",
                    "description": detail,
                    "location": "전체 페이지",
                    "element_type": "페이지 로드",
                    "selector": "전체 페이지",
                    "code": f"현재 로딩 시간: {load_time:.0f}ms\n목표: 3000ms 이하",
                    "fix_step1": fix1,
                    "fix_step2": fix2,
                    "fix_code": "/* 이미지 최적화 예시 */\n<img src='image.webp' loading='lazy' width='800' height='600' alt='...'>\n\n/* 스크립트 지연 로딩 */\n<script src='app.js' defer></script>",
                    "issue_type": "PERFORMANCE"
                })
            
            # 이슈 마커 표시
            self.draw_issue_markers(screenshot_path, all_issues)
            
            # CSV 기록
            for issue in all_issues:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                
                self.csv_writer.writerow({
                    "번호": self.issue_count - len(all_issues) + all_issues.index(issue) + 1,
                    "URL": url,
                    "품질주특성": issue["main_char"],
                    "품질부특성": issue["sub_char"],
                    "중요도": issue["severity"],
                    "우선순위": issue.get("priority", "중간"),
                    "시간": timestamp,
                    "문제요약": issue.get("title", issue.get("description", "")[:50]),
                    "위치": issue.get("location", ""),
                    "요소타입": issue.get("element_type", ""),
                    "문제상세": issue.get("description", ""),
                    "CSS선택자": issue.get("selector", ""),
                    "현재코드": issue.get("code", ""),
                    "수정1단계": issue.get("fix_step1", ""),
                    "수정2단계": issue.get("fix_step2", ""),
                    "수정코드예시": issue.get("fix_code", ""),
                    "스크린샷": screenshot_name
                })
                
                self.logger.warning(
                    f"  [{self.issue_count - len(all_issues) + all_issues.index(issue) + 1}] "
                    f"[{issue.get('priority', '중간')}] "
                    f"{issue['main_char']}/{issue['sub_char']} (중요도: {issue['severity']}) - "
                    f"{issue.get('title', issue.get('description', ''))[:60]}"
                )
            
            return all_issues
            
        except Exception as e:
            self.logger.error(f"페이지 분석 실패 ({url}): {e}")
            return []
    
    def extract_links(self, page: Page, base_url: str) -> List[str]:
        """링크 추출 - Frame 지원 (디버깅 강화)"""
        links = []
        try:
            base_domain = urlparse(self.base_url).netloc
            self.logger.info(f"  🔍 링크 추출 시작 (기준 도메인: {base_domain})")
            
            # 메인 페이지 링크
            js_result = page.evaluate("""
                () => {
                    const links = [];
                    document.querySelectorAll('a[href]').forEach(a => {
                        const href = a.getAttribute('href');
                        if (href && href.trim() !== '' && href !== '#' && 
                            !href.startsWith('javascript:')) {
                            links.push(href);
                        }
                    });
                    return links;
                }
            """)
            self.logger.info(f"    - 메인 페이지 <a> 태그: {len(js_result)}개")
            
            # Frame src 추출
            frame_srcs = page.evaluate("""
                () => {
                    const srcs = [];
                    document.querySelectorAll('frame[src], iframe[src]').forEach(f => {
                        const src = f.getAttribute('src');
                        if (src && src.trim() !== '' && !src.startsWith('javascript:')) {
                            srcs.push(src);
                        }
                    });
                    return srcs;
                }
            """)
            
            if frame_srcs:
                self.logger.info(f"    - Frame/iframe src: {len(frame_srcs)}개")
                js_result.extend(frame_srcs)
            
            # Frame 내부 링크
            try:
                frames = page.frames
                self.logger.info(f"    - 전체 frame 개수: {len(frames)}개")
                
                frame_link_count = 0
                for idx, frame in enumerate(frames):
                    if frame != page.main_frame:
                        try:
                            frame_links = frame.evaluate("""
                                () => {
                                    const links = [];
                                    document.querySelectorAll('a[href]').forEach(a => {
                                        const href = a.getAttribute('href');
                                        if (href && href.trim() !== '' && href !== '#' && 
                                            !href.startsWith('javascript:')) {
                                            links.push(href);
                                        }
                                    });
                                    return links;
                                }
                            """)
                            if frame_links:
                                frame_link_count += len(frame_links)
                                js_result.extend(frame_links)
                        except Exception as e:
                            self.logger.debug(f"    - Frame {idx} 링크 추출 실패: {e}")
                
                if frame_link_count > 0:
                    self.logger.info(f"    - Frame 내부 링크: {frame_link_count}개")
            except Exception as e:
                self.logger.debug(f"    - Frame API 사용 실패: {e}")
            
            self.logger.info(f"  📊 원본 링크 총 {len(js_result)}개 추출")
            
            # 절대 URL 변환 및 필터링
            for href in js_result:
                try:
                    absolute_url = urljoin(base_url, href.strip())
                    clean_url, _ = urldefrag(absolute_url)
                    link_domain = urlparse(clean_url).netloc
                    
                    if link_domain == base_domain:
                        links.append(clean_url)
                    else:
                        self.logger.debug(f"    ✗ 다른 도메인: {link_domain} - {clean_url[:60]}")
                except Exception as e:
                    self.logger.debug(f"    ✗ URL 처리 실패 ({href[:60]}): {e}")
            
            unique_links = list(set(links))
            self.logger.info(f"  ✅ 같은 도메인 링크: {len(unique_links)}개 (중복 제거)")
            
            # 샘플 링크 출력
            if unique_links:
                for link in unique_links[:3]:
                    self.logger.info(f"     • {link}")
                if len(unique_links) > 3:
                    self.logger.info(f"     ... 외 {len(unique_links) - 3}개")
            else:
                self.logger.warning(f"  ⚠️ 같은 도메인의 링크가 없습니다!")
            
            return unique_links
            
        except Exception as e:
            self.logger.error(f"링크 추출 오류: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return []
    
    def crawl(self):
        """크롤링 실행"""
        self.logger.info(f"🚀 웹 품질 점검 시작 (Gemini AI 모드)")
        self.logger.info(f"📊 BASE_URL: {self.base_url}")
        self.logger.info(f"📄 MAX_PAGES: {self.max_pages}개")
        self.logger.info(f"📁 REPORT_DIR: {self.report_dir}")
        self.logger.info("=" * 70)
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 720})
            page = context.new_page()
            
            while self.queue and self.page_count < self.max_pages:
                url = self.queue.popleft()
                
                if url in self.visited:
                    self.logger.debug(f"  ⏭️ 이미 방문: {url}")
                    continue
                
                self.visited.add(url)
                self.page_count += 1
                
                self.logger.info("")
                self.logger.info("=" * 70)
                self.logger.info(f"[{self.page_count}/{self.max_pages}] {url}")
                
                # 페이지 분석
                self.analyze_page(page, url)
                
                # 링크 추출
                if self.page_count < self.max_pages:
                    try:
                        self.logger.info("")
                        new_links = self.extract_links(page, url)
                        
                        added = 0
                        skipped_visited = 0
                        skipped_queued = 0
                        
                        for link in new_links:
                            if link in self.visited:
                                skipped_visited += 1
                            elif link in self.queue:
                                skipped_queued += 1
                            else:
                                self.queue.append(link)
                                added += 1
                                self.logger.debug(f"     + 큐 추가: {link}")
                        
                        self.logger.info(f"  📥 큐 업데이트:")
                        self.logger.info(f"     - 추가됨: {added}개")
                        self.logger.info(f"     - 이미 방문: {skipped_visited}개")
                        self.logger.info(f"     - 이미 큐에 있음: {skipped_queued}개")
                        self.logger.info(f"     - 현재 큐 크기: {len(self.queue)}개")
                        
                        # 큐에 남은 링크 샘플 출력
                        if len(self.queue) > 0:
                            self.logger.info(f"  📋 큐에 대기 중인 링크 (샘플):")
                            for q_link in list(self.queue)[:3]:
                                self.logger.info(f"     - {q_link}")
                            if len(self.queue) > 3:
                                self.logger.info(f"     ... 외 {len(self.queue) - 3}개")
                        else:
                            self.logger.warning(f"  ⚠️ 큐가 비어있습니다! 더 이상 방문할 페이지가 없습니다.")
                        
                    except Exception as e:
                        self.logger.error(f"링크 추출 실패: {e}")
                        import traceback
                        self.logger.error(traceback.format_exc())
                
                time.sleep(DELAY)
            
            browser.close()
        
        self.csv_file.close()
        
        self.logger.info("=" * 70)
        self.logger.info(f"✅ 점검 완료!")
        self.logger.info(f"📄 총 페이지: {self.page_count}개")
        self.logger.info(f"⚠️ 총 이슈: {self.issue_count}개")
        self.logger.info(f"📁 리포트: {self.report_dir}")
        self.logger.info(f"📊 CSV: quality_report_{self.timestamp}.csv")
        self.logger.info("=" * 70)


def main():
    """메인 실행"""
    if len(sys.argv) < 2:
        print("사용법: python web_check.py <페이지개수>")
        print("예: python web_check.py 50")
        sys.exit(1)
    
    try:
        max_pages = int(sys.argv[1])
        if max_pages < 1:
            print("오류: 페이지 개수는 1 이상이어야 합니다.")
            sys.exit(1)
    except ValueError:
        print("오류: 페이지 개수는 정수여야 합니다.")
        sys.exit(1)
    
    checker = WebQualityChecker(BASE_URL, max_pages, GEMINI_API_KEY)
    checker.crawl()


if __name__ == "__main__":
    main()