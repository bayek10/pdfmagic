import fitz  # PyMuPDF
import google.generativeai as genai
import json
import re
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class PDFProcessor:
    def __init__(self, pdf_path: str, gemini_api_key: str):
        self.pdf_path = pdf_path
        # Initialize Gemini
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel("gemini-1.5-pro-002")
        self.BATCH_SIZE = 2  # Pages per batch
        
    def extract_product_info(self) -> List[Dict]:
        """Main method to process PDF and return structured product data"""
        # Extract text from PDF
        extracted_text = self._extract_text_from_pdf()
        
        # Process text through LLM in batches
        all_products = self._process_text_batches(extracted_text)
        
        return all_products

    def _extract_text_from_pdf(self) -> Dict[int, str]:
        """Extract text from PDF using PyMuPDF (fitz)"""
        extracted_text = {}
        try:
            doc = fitz.open(self.pdf_path)
            for page_num in range(doc.page_count):
                page = doc[page_num]
                text = page.get_text()
                extracted_text[page_num + 1] = text
            doc.close()
            return extracted_text
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {str(e)}")
            raise

    def _create_prompt(self, page_texts: List[str], page_numbers: List[int]) -> str:
        """Create prompt for LLM"""
        combined_text = "\n".join([f"TEXT FROM PAGE {num}:\n\"{text}\"" 
                                 for num, text in zip(page_numbers, page_texts)])
        
        return f"""
        The following is text extracted from pages {page_numbers} of a PDF furniture catalog. 
        It contains information for 1 or more products that the company sells. 
        Each product can have 1 or more price tables. Extract each product on the page 
        in the same language (Italian) and output the data as a JSON array, with each 
        product represented as a JSON object with the attributes listed below:
        
        ATTRIBUTES:
        - product_name: name of the product
        - brand_name: name of the brand
        - designer: name of the designer
        - year: year of manufacture
        - type_of_product: type of product (e.g., sofa, table, etc.)
        - all_colors: an array of all colors mentioned for the product
        - page_reference: an object containing the PDF file path as a string and 
          the page numbers of the product as an array

        {combined_text}
        """

    def _parse_text_with_gemini(self, page_texts: List[str], 
                               page_numbers: List[int]) -> Optional[str]:
        """Send text to Gemini API and get structured response"""
        try:
            prompt = self._create_prompt(page_texts, page_numbers)
            response = self.model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                )
            )
            return response.candidates[0].content.parts[0].text
        except Exception as e:
            logger.error(f"Error calling Gemini API: {str(e)}")
            return None

    def _extract_json_from_response(self, response_text: str) -> Optional[List[Dict]]:
        """Extract JSON data from LLM response"""
        json_match = re.search(r'\[\s*{.*}\s*\]', response_text, re.DOTALL)
        if not json_match:
            return None
            
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            logger.error("Error decoding JSON from LLM response")
            return None

    def _process_text_batches(self, extracted_text: Dict[int, str]) -> List[Dict]:
        """Process extracted text in batches through LLM"""
        all_products = []
        page_texts_batch = []
        page_numbers_batch = []

        for page_num, page_text in extracted_text.items():
            page_texts_batch.append(page_text)
            page_numbers_batch.append(page_num)

            if len(page_texts_batch) == self.BATCH_SIZE:
                products = self._process_batch(page_texts_batch, page_numbers_batch)
                if products:
                    all_products.extend(products)
                page_texts_batch = []
                page_numbers_batch = []

        # Process remaining pages
        if page_texts_batch:
            products = self._process_batch(page_texts_batch, page_numbers_batch)
            if products:
                all_products.extend(products)

        return all_products

    def _process_batch(self, page_texts: List[str], page_numbers: List[int]) -> Optional[List[Dict]]:
        """Process a single batch of pages"""
        structured_data = self._parse_text_with_gemini(page_texts, page_numbers)
        if not structured_data:
            return None

        json_batch_data = self._extract_json_from_response(structured_data)
        if json_batch_data:
            for product in json_batch_data:
                # Only update the file path, preserve the page numbers from LLM
                if "page_reference" not in product:
                    # Fallback if LLM didn't provide page numbers
                    product["page_reference"] = {
                        "file_path": self.pdf_path,
                        "page_numbers": [page_numbers[0]]
                    }
                else:
                    # Keep LLM's page numbers, just update the file path
                    product["page_reference"]["file_path"] = self.pdf_path
            return json_batch_data
        return None