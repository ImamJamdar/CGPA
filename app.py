from flask import Flask, request, jsonify
from flask_cors import CORS
import pdfplumber
import re
import io
import logging
from werkzeug.utils import secure_filename
import os
import tempfile
from difflib import SequenceMatcher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

GRADE_POINTS = {"O": 10, "A+": 9, "A": 8, "B+": 7, "B": 6, "C": 5, "P": 4, "F": 0}
ALLOWED_EXTENSIONS = {'pdf'}
DEPARTMENT_CODES = {
    "CV": "Civil Engineering",
    "ME": "Mechanical Engineering",
    "ES": "Electrical and Electronics Engineering",
    "EC": "Electronics and Communication Engineering",
    "IM": "Industrial Engineering and Management",
    "CS": "Computer Science and Engineering",
    "ET": "Electronics and Telecommunication Engineering",
    "IS": "Information Science and Engineering",
    "EI": "Electronics and Instrumentation Engineering",
    "MD": "Medical Electronics Engineering",
    "CH": "Chemical Engineering",
    "BT": "Bio-Technology",
    "AS": "Aerospace Engineering",
    "AM": "Machine Learning (AI and ML)",
    "DS": "Computer Science and Engineering (DS)",
    "DC": "Computer Science and Engineering (IoT and CS)",
    "AI": "Artificial Intelligence and Data Science",
    "BS": "Computer Science and Business Systems"
}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_pdf(pdf_file):
    try:
        text = ""
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text += page.extract_text() + "\n"
        return text
    except Exception as e:
        logger.error(f"PDF extraction error: {str(e)}")
        raise ValueError("Unable to extract text from the provided PDF")

def detect_department(result_text, course_text):
    all_text = result_text + course_text
    dept_counts = {}
    for dept_code, dept_name in DEPARTMENT_CODES.items():
        pattern = fr'\b2\d{dept_code}\d'
        matches = re.findall(pattern, all_text)
        dept_counts[dept_code] = len(matches)
    
    max_dept = max(dept_counts.items(), key=lambda x: x[1]) if dept_counts else (None, 0)
    if max_dept[1] > 0:
        return max_dept[0], DEPARTMENT_CODES.get(max_dept[0])
    return None, None

def detect_semester(result_text, course_text):
    all_text = result_text + course_text
    sem_counts = {}
    for i in range(1, 9):
        pattern = fr'\b2\d[A-Za-z]{2}{i}[A-Za-z]{2}'
        matches = re.findall(pattern, all_text)
        sem_counts[i] = len(matches)
    
    max_sem = max(sem_counts.items(), key=lambda x: x[1]) if sem_counts else (None, 0)
    if max_sem[1] > 0:
        return max_sem[0]
    return None

def normalize_subject_code(code):
    code = code.strip().replace(" ", "")
    match = re.search(r'(\d+)([A-Za-z]+)(\d+)([A-Za-z]+)([A-Za-z0-9]+)', code)
    if match:
        return match.group(0)
    return code

def extract_core_code_parts(code):
    match = re.search(r'(\d+)([A-Za-z]+)(\d+)([A-Za-z]+)', code)
    if match:
        year = match.group(1)
        dept = match.group(2)
        sem = match.group(3)
        type_code = match.group(4)
        return year, dept, sem, type_code
    return None, None, None, None

def parse_result_data(result_text):
    subjects = {}
    subject_lines = []
    lines = result_text.split('\n')

    for i, line in enumerate(lines):
        if re.search(r'\b2\d[A-Za-z]{2}\d[A-Za-z]{2,7}', line):
            subject_lines.append((i, line))
    
    for i, (line_idx, line) in enumerate(subject_lines):
        code_match = re.search(r'(2\d[A-Za-z]{2}\d[A-Za-z]{2,7}[A-Za-z0-9]*)', line)
        if not code_match:
            continue
        
        subject_code = code_match.group(1).strip()
        remaining_text = line[code_match.end():].strip()
        subject_name = re.sub(r'\s+\d+.*$', '', remaining_text).strip()
        
        if not subject_name or subject_name.isdigit():
            if line_idx + 1 < len(lines) and line_idx + 1 not in [idx for idx, _ in subject_lines]:
                next_line = lines[line_idx + 1]
                if not re.search(r'\b2\d[A-Za-z]{2}\d[A-Za-z]{2,7}', next_line):
                    subject_name = next_line.strip()
        
        grade_match = re.search(r'[0-9]+\s+([OABCPFo+]+)$', line)
        grade = None
        if grade_match:
            grade = grade_match.group(1).strip().upper().replace(" ", "")
        else:
            for j in range(1, 3):
                if line_idx + j < len(lines):
                    next_line = lines[line_idx + j]
                    grade_match = re.search(r'([OABCPFo+]+)$', next_line)
                    if grade_match and len(next_line) < 20:
                        grade = grade_match.group(1).strip().upper().replace(" ", "")
                        break
        
        if grade == "O+":
            grade = "O"
            
        if grade in GRADE_POINTS:
            subjects[subject_code] = {
                "name": subject_name,
                "grade": grade,
                "normalized_code": normalize_subject_code(subject_code)
            }

    # Look for grades in structured format
    for i, line in enumerate(lines):
        if "GRADES" in line and i < len(lines) - 1:
            for j in range(i+1, min(i+20, len(lines))):
                grade_line = lines[j]
                full_match = re.search(r'(2\d[A-Za-z]{2}\d[A-Za-z]{2,7}[A-Za-z0-9]*)\s+(.*?)\s+\d+\s+\d+\s+\d+\s+([OABCPFo+]+)', grade_line)
                if full_match:
                    subject_code = full_match.group(1).strip()
                    subject_name = full_match.group(2).strip()
                    grade = full_match.group(3).strip().upper().replace(" ", "")
                    
                    if grade in GRADE_POINTS:
                        subjects[subject_code] = {
                            "name": subject_name,
                            "grade": grade,
                            "normalized_code": normalize_subject_code(subject_code)
                        }

    # Special subjects pattern matching
    special_subject_patterns = [
        (r'(2\d[A-Za-z]{2}\d[A-Za-z]{2,7}(?:BFE|FBE))\s+(Biology\s+for\s+Engineers)', "Biology for Engineers"),
        (r'(2\d[A-Za-z]{2}\d[A-Za-z]{2,7}ENV)\s+(Environmental\s+Studies)', "Environmental Studies"),
        (r'(2\d[A-Za-z]{2}\d[A-Za-z]{2,7}CPH)\s+(Constitution\s+of\s+India)', "Constitution of India"),
        (r'(2\d[A-Za-z]{2}\d[A-Za-z]{2,7}MAT)\s+(Mathematics)', "Mathematics")
    ]
    
    for pattern, name in special_subject_patterns:
        for line in lines:
            special_match = re.search(pattern, line, re.IGNORECASE)
            if special_match:
                subject_code = special_match.group(1).strip()
                subject_name = name
                grade_match = re.search(r'([OABCPFo+]+)$', line)
                if grade_match:
                    grade = grade_match.group(1).strip().upper().replace(" ", "")
                    if grade in GRADE_POINTS:
                        subjects[subject_code] = {
                            "name": subject_name,
                            "grade": grade,
                            "normalized_code": normalize_subject_code(subject_code)
                        }

    # Search for special subject keywords
    for i, line in enumerate(lines):
        for keyword in ["Biology for Engineers", "Environmental Studies", "Constitution of India"]:
            if keyword in line:
                code_match = re.search(r'(2\d[A-Za-z]{2}\d[A-Za-z]{2,7}[A-Za-z0-9]*)', line)
                if code_match:
                    subject_code = code_match.group(1).strip()
                    grade_match = re.search(r'([OABCPFo+]+)$', line)
                    if grade_match:
                        grade = grade_match.group(1).strip().upper().replace(" ", "")
                        if grade in GRADE_POINTS:
                            subjects[subject_code] = {
                                "name": keyword,
                                "grade": grade,
                                "normalized_code": normalize_subject_code(subject_code)
                            }
                    else:
                        for j in range(1, 3):
                            if i + j < len(lines):
                                next_line = lines[i + j]
                                grade_match = re.search(r'([OABCPFo+]+)$', next_line)
                                if grade_match and len(next_line) < 20:
                                    grade = grade_match.group(1).strip().upper().replace(" ", "")
                                    if grade in GRADE_POINTS:
                                        subjects[subject_code] = {
                                            "name": keyword,
                                            "grade": grade,
                                            "normalized_code": normalize_subject_code(subject_code)
                                        }
                                    break

    logger.info(f"Parsed {len(subjects)} subjects with grades")
    return subjects

def parse_course_data(course_text):
    course_credits = {}
    subject_names = {}
    lines = course_text.split('\n')
    in_course_section = False
    
    for i, line in enumerate(lines):
        if "Course Details" in line or "Course Title" in line:
            in_course_section = True
            continue
        
        if in_course_section and "Total Credits" in line:
            in_course_section = False
            continue
        
        if in_course_section:
            patterns = [
                r'\d+\s+(.+?)\s+(2\d[A-Za-z0-9]{6,10})\s+\w+\s+\d+\s+(\d+(?:\.\d+)?)',
                r'(.+?)\s+(2\d[A-Za-z0-9]{6,10})\s+\w+\s+\d+\s+(\d+(?:\.\d+)?)',
                r'(.+?)\s+(2\d[A-Za-z0-9]{6,10})\s+.*?(\d+(?:\.\d+)?)$'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    subject_name = match.group(1).strip()
                    subject_code = match.group(2).strip()
                    credit = float(match.group(3).strip())
                    normalized_code = normalize_subject_code(subject_code)
                    
                    course_credits[subject_code] = credit
                    course_credits[normalized_code] = credit
                    subject_names[subject_code] = subject_name
                    subject_names[normalized_code] = subject_name
                    break
    
    # Special subject pattern matching
    special_subject_patterns = [
        (r'(Biology\s+for\s+Engineers)\s+(2\d[A-Za-z0-9]{6,10})', "BFE|FBE"),
        (r'(Environmental\s+Studies)\s+(2\d[A-Za-z0-9]{6,10})', "ENV"),
        (r'(Constitution\s+of\s+India)\s+(2\d[A-Za-z0-9]{6,10})', "CPH"),
        (r'(Mathematics)\s+(2\d[A-Za-z0-9]{6,10})', "MAT")
    ]
    
    for pattern, identifier in special_subject_patterns:
        for line in lines:
            special_match = re.search(pattern, line, re.IGNORECASE)
            if special_match:
                subject_name = special_match.group(1).strip()
                subject_code = special_match.group(2).strip()
                credit_match = re.search(r'(\d+(?:\.\d+)?)$', line)
                if credit_match:
                    credit = float(credit_match.group(1).strip())
                    normalized_code = normalize_subject_code(subject_code)
                    
                    course_credits[subject_code] = credit
                    course_credits[normalized_code] = credit
                    subject_names[subject_code] = subject_name
                    subject_names[normalized_code] = subject_name
    
    # Generate alternative department codes
    for code in list(course_credits.keys()):
        year, dept, sem, type_code = extract_core_code_parts(code)
        if year and dept and sem:
            credit = course_credits[code]
            name = subject_names.get(code, "")
            
            for alt_dept in DEPARTMENT_CODES:
                if alt_dept != dept:
                    alt_code = code.replace(dept, alt_dept)
                    alt_normalized = normalize_subject_code(alt_code)
                    course_credits[alt_code] = credit
                    course_credits[alt_normalized] = credit
                    subject_names[alt_code] = name
                    subject_names[alt_normalized] = name
    
    # Create subject name mapping
    subject_name_map = {}
    for code, name in subject_names.items():
        name_key = name.lower().replace(" ", "")
        subject_name_map[name_key] = code
    
    logger.info(f"Parsed {len(course_credits)} subjects with credits and {len(subject_names)} subject names")
    return course_credits, subject_names, subject_name_map

def find_matching_code(subject_code, course_credits, subject_names, subject_name_map, result_subject_data):
    normalized_code = normalize_subject_code(subject_code)
    
    # Direct match with normalized code
    if normalized_code in course_credits:
        return normalized_code
    
    # Match by subject name
    subject_name = result_subject_data["name"].lower().replace(" ", "")
    if subject_name in subject_name_map:
        matching_code = subject_name_map[subject_name]
        if matching_code in course_credits:
            return matching_code
    
    # Extract core parts of the code
    year, dept, sem, type_code = extract_core_code_parts(normalized_code)
    if not all([year, dept, sem, type_code]):
        return None
    
    # Special subject matching
    special_keywords = {
        "BFE": "Biology for Engineers",
        "FBE": "Biology for Engineers",
        "ENV": "Environmental Studies",
        "CPH": "Constitution of India",
        "MAT": "Mathematics"
    }
    
    for keyword, subject in special_keywords.items():
        if keyword in subject_code or subject in result_subject_data["name"]:
            for code in course_credits:
                if keyword in code:
                    return code
    
    # Match by year, semester and subject name
    for code in course_credits:
        year2, dept2, sem2, type2 = extract_core_code_parts(code)
        if year2 == year and sem2 == sem:
            if result_subject_data["name"] and len(result_subject_data["name"]) > 3:
                search_term = result_subject_data["name"].lower().split()[0]
                if search_term in subject_names.get(code, "").lower():
                    return code
    
    # Match by core pattern (year + semester + type)
    core_pattern = f"{year}{sem}{type_code[:1]}"
    for code in course_credits:
        if core_pattern in code:
            return code
    
    # Fuzzy matching as last resort
    best_match = None
    highest_similarity = 0
    for code in course_credits:
        if f"{sem}" in code:
            similarity = SequenceMatcher(None, normalized_code, code).ratio()
            if similarity > highest_similarity:
                highest_similarity = similarity
                best_match = code
    
    if highest_similarity > 0.5:
        return best_match
    
    # Fall back to just semester matching
    for code in course_credits:
        year2, dept2, sem2, type2 = extract_core_code_parts(code)
        if sem2 == sem:
            return code
    
    return None

def combine_data(subjects, course_credits, subject_names, subject_name_map):
    combined_data = {}
    unmatched_subjects = []
    
    for subject_code, subject_data in subjects.items():
        credit = None
        name = subject_data["name"]
        normalized_code = subject_data["normalized_code"]
        
        if subject_code in course_credits:
            credit = course_credits[subject_code]
        elif normalized_code in course_credits:
            credit = course_credits[normalized_code]
        else:
            matching_code = find_matching_code(subject_code, course_credits, subject_names, subject_name_map, subject_data)
            if matching_code:
                credit = course_credits[matching_code]
                if matching_code in subject_names:
                    name = subject_names[matching_code]
        
        if credit is not None:
            if subject_code in subject_names and (name.isdigit() or not name or len(name) < 3):
                name = subject_names[subject_code]
            elif normalized_code in subject_names and (name.isdigit() or not name or len(name) < 3):
                name = subject_names[normalized_code]
            
            combined_data[subject_code] = {
                "name": name,
                "credit": credit,
                "grade": subject_data["grade"],
                "grade_point": GRADE_POINTS.get(subject_data["grade"], 0),
                "weighted_point": credit * GRADE_POINTS.get(subject_data["grade"], 0)
            }
        else:
            unmatched_subjects.append({
                "code": subject_code,
                "name": name,
                "grade": subject_data["grade"]
            })
    
    if unmatched_subjects:
        logger.info(f"Unmatched subjects: {len(unmatched_subjects)}")
        for subj in unmatched_subjects:
            logger.info(f" {subj['code']} - {subj['name']} - {subj['grade']}")
            
            # Handle special cases
            for keyword, pattern in [
                ("Biology", r"BFE|FBE"),
                ("Environment", r"ENV"),
                ("Constitution", r"CPH"),
                ("Math", r"MAT")
            ]:
                if keyword in subj["name"]:
                    matching_codes = [code for code in course_credits if re.search(pattern, code)]
                    if matching_codes:
                        code = matching_codes[0]
                        credit = course_credits[code]
                        name = subject_names.get(code, subj["name"])
                        combined_data[subj["code"]] = {
                            "name": name,
                            "credit": credit,
                            "grade": subj["grade"],
                            "grade_point": GRADE_POINTS.get(subj["grade"], 0),
                            "weighted_point": credit * GRADE_POINTS.get(subj["grade"], 0)
                        }
                        break
            
            # Try to match by semester and type
            year, dept, sem, type_code = extract_core_code_parts(subj["code"])
            if sem:
                for code in course_credits:
                    _, _, code_sem, code_type = extract_core_code_parts(code)
                    if code_sem == sem and (type_code == code_type or type_code[0] == code_type[0]):
                        credit = course_credits[code]
                        name = subject_names.get(code, subj["name"])
                        combined_data[subj["code"]] = {
                            "name": name,
                            "credit": credit,
                            "grade": subj["grade"],
                            "grade_point": GRADE_POINTS.get(subj["grade"], 0),
                            "weighted_point": credit * GRADE_POINTS.get(subj["grade"], 0)
                        }
                        break
    
    logger.info(f"Combined data for {len(combined_data)} subjects")
    return combined_data

def calculate_sgpa(subjects):
    total_credits = 0
    weighted_sum = 0
    subject_points = []
    
    for subject_code, data in subjects.items():
        if "credit" not in data or data["credit"] == 0:
            continue
        
        credit = data["credit"]
        grade = data["grade"]
        grade_point = GRADE_POINTS.get(grade, 0)
        weighted_point = credit * grade_point
        
        subject_points.append({
            "code": subject_code,
            "name": data["name"],
            "credit": credit,
            "grade": grade,
            "grade_point": grade_point,
            "weighted_point": weighted_point
        })
        
        total_credits += credit
        weighted_sum += weighted_point
    
    if total_credits <= 0:
        return 0, subject_points, total_credits, weighted_sum
    
    sgpa = weighted_sum / total_credits
    logger.info(f"Calculated SGPA: {round(sgpa, 2)} (Total credits: {total_credits}, Total points: {weighted_sum})")
    
    return round(sgpa, 2), subject_points, total_credits, weighted_sum

def calculate_cgpa(semester_data):
    """
    Calculate CGPA based on multiple semesters.
    Formula: Total points earned across all semesters / Total credits across all semesters
    
    Also calculates running CGPA for each semester.
    """
    cumulative_credits = 0
    cumulative_points = 0
    semester_cgpa = {}
    
    # Sort semesters by ID
    sorted_semesters = sorted(semester_data.items(), key=lambda x: x[0])
    
    for sem_id, data in sorted_semesters:
        # Add current semester credits and points to cumulative totals
        cumulative_credits += data["total_credits"]
        cumulative_points += data["total_points"]
        
        # Calculate CGPA up to this semester
        if cumulative_credits > 0:
            cgpa = round(cumulative_points / cumulative_credits, 2)
        else:
            cgpa = 0
            
        # Store CGPA for this semester
        semester_cgpa[sem_id] = cgpa
        
        # Update the semester data with CGPA
        semester_data[sem_id]["cgpa"] = cgpa
    
    # Overall CGPA
    overall_cgpa = round(cumulative_points / cumulative_credits, 2) if cumulative_credits > 0 else 0
    
    # Summary data
    summary = {
        "total_credits": round(cumulative_credits, 1),
        "total_points": round(cumulative_points, 1),
        "max_possible_points": round(cumulative_credits * 10, 1),
        "overall_percentage": round((cumulative_points / (cumulative_credits * 10)) * 100, 2) if cumulative_credits > 0 else 0
    }
    
    logger.info(f"Calculated overall CGPA: {overall_cgpa}")
    logger.info(f"Semester-wise CGPA: {semester_cgpa}")
    
    return overall_cgpa, semester_data, summary

def generate_report(subject_points, sgpa, total_credits, total_points, dept_code, dept_name, semester):
    report = {
        "sgpa": sgpa,
        "department": {
            "code": dept_code,
            "name": dept_name
        },
        "semester": semester,
        "subjects": {},
        "summary": {
            "total_credits": round(total_credits, 1),
            "total_points": round(total_points, 1),
            "max_possible_points": round(total_credits * 10, 1),
            "percentage": round((total_points / (total_credits * 10)) * 100, 2) if total_credits > 0 else 0
        }
    }
    
    for point in subject_points:
        subject_name = point["name"]
        if subject_name.isdigit() or not subject_name:
            subject_name = f"Subject {point['code']}"
            
        report["subjects"][subject_name] = {
            "code": point["code"],
            "credit": point["credit"],
            "grade": point["grade"],
            "grade_point": point["grade_point"],
            "weighted_point": round(point["weighted_point"], 2)
        }
    
    return report

def process_semester_files(course_file, result_file, sem_id=None):
    """Process a single semester's course and result files"""
    try:
        course_text = extract_text_from_pdf(course_file)
        result_text = extract_text_from_pdf(result_file)
        
        # Detect department and semester
        dept_code, dept_name = detect_department(result_text, course_text)
        semester = detect_semester(result_text, course_text)
        
        if not dept_code:
            logger.warning("Could not automatically detect department")
        else:
            logger.info(f"Detected department: {dept_code} ({dept_name})")
            
        if not semester:
            logger.warning("Could not automatically detect semester")
        else:
            logger.info(f"Detected semester: {semester}")
        
        subjects_with_grades = parse_result_data(result_text)
        if not subjects_with_grades:
            raise ValueError("No subjects found in the results PDF. Please check the file.")
        
        course_credits, subject_names, subject_name_map = parse_course_data(course_text)
        if not course_credits:
            raise ValueError("No course credits found in the course PDF. Please check the file.")
        
        combined_data = combine_data(subjects_with_grades, course_credits, subject_names, subject_name_map)
        if not combined_data:
            raise ValueError("Could not match any subjects between the two files. Please check that both files are for the same semester.")
        
        sgpa, subject_points, total_credits, total_points = calculate_sgpa(combined_data)
        
        # Generate detailed report
        report = generate_report(subject_points, sgpa, total_credits, total_points, dept_code, dept_name, semester)
        
        # Log detailed calculation
        logger.info("\n----- SGPA CALCULATION SUMMARY -----")
        logger.info(f"{'SUBJECT CODE':<15} {'SUBJECT NAME':<40} {'CREDITS':<10} {'GRADE':<8} {'POINTS':<8} {'WEIGHTED':<10}")
        logger.info("-" * 90)
        
        for point in subject_points:
            logger.info(f"{point['code']:<15} {point['name']:<40} {point['credit']:<10.1f} {point['grade']:<8} {point['grade_point']:<8} {point['weighted_point']:<10.1f}")
        
        logger.info("-" * 90)
        logger.info(f"DEPARTMENT: {dept_name if dept_name else 'Unknown'}")
        logger.info(f"SEMESTER: {semester if semester else 'Unknown'}")
        logger.info(f"TOTAL CREDITS: {total_credits:.1f}")
        logger.info(f"TOTAL POINTS: {total_points:.1f}")
        logger.info(f"SGPA: {sgpa:.2f}")
        logger.info("-" * 90)
        
        return {
            "sgpa": sgpa,
            "subjects": report["subjects"],
            "total_credits": total_credits,
            "total_points": total_points,
            "max_possible_points": total_credits * 10,
            "percentage": round((total_points / (total_credits * 10)) * 100, 2) if total_credits > 0 else 0,
            "department": {
                "code": dept_code,
                "name": dept_name
            },
            "semester": semester
        }
    
    except Exception as e:
        logger.error(f"Error processing semester {sem_id}: {str(e)}")
        raise

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "healthy", "version": "3.0.0"})

@app.route("/upload", methods=["POST"])
def upload_files():
    """Handle single semester SGPA calculation"""
    try:
        if "results" not in request.files or "courses" not in request.files:
            return jsonify({"error": "Both course and result PDFs are required"}), 400
        
        course_pdf = request.files["courses"]
        result_pdf = request.files["results"]
        
        for file in [course_pdf, result_pdf]:
            if file.filename == '':
                return jsonify({"error": "No file selected"}), 400
            if not allowed_file(file.filename):
                return jsonify({"error": "File must be a PDF"}), 400
        
        # Create temporary files to handle the uploaded PDFs
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as course_temp:
            course_path = course_temp.name
            course_pdf.save(course_path)
        
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as result_temp:
            result_path = result_temp.name
            result_pdf.save(result_path)
        
        try:
            # Process the files
            semester_data = process_semester_files(course_path, result_path)
            
            # Prepare response
            response = {
                "sgpa": semester_data["sgpa"],
                "subjects": semester_data["subjects"],
                "summary": {
                    "total_credits": semester_data["total_credits"],
                    "total_points": semester_data["total_points"],
                    "max_possible_points": semester_data["max_possible_points"],
                    "percentage": semester_data["percentage"]
                }
            }
            
            return jsonify(response)
        
        finally:
            # Clean up temporary files
            try:
                os.unlink(course_path)
                os.unlink(result_path)
            except Exception as e:
                logger.error(f"Error removing temporary files: {e}")
                
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Unexpected error in upload_files: {str(e)}")
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500

@app.route("/calculate_cgpa", methods=["POST"])
def calculate_cgpa_route():
    """Handle multi-semester CGPA calculation"""
    try:
        # Verify semester count is provided
        if "semester_count" not in request.form:
            return jsonify({"error": "Semester count is required"}), 400
        
        semester_count = int(request.form["semester_count"])
        if semester_count <= 0 or semester_count > 8:
            return jsonify({"error": "Invalid semester count. Must be between 1 and 8."}), 400
        
        # Setup dictionary to store semester data
        semester_data = {}
        temp_files = []
        
        try:
            # Process each semester's files
            for sem_id in range(1, semester_count + 1):
                course_key = f"courses_{sem_id}"
                result_key = f"results_{sem_id}"
                
                if course_key not in request.files or result_key not in request.files:
                    return jsonify({"error": f"Files for semester {sem_id} are missing"}), 400
                
                course_pdf = request.files[course_key]
                result_pdf = request.files[result_key]
                
                # Validate files
                for file in [course_pdf, result_pdf]:
                    if file.filename == '':
                        return jsonify({"error": f"No file selected for semester {sem_id}"}), 400
                    if not allowed_file(file.filename):
                        return jsonify({"error": f"File must be a PDF for semester {sem_id}"}), 400
                
                # Create temporary files
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as course_temp:
                    course_path = course_temp.name
                    course_pdf.save(course_path)
                    temp_files.append(course_path)
                
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as result_temp:
                    result_path = result_temp.name
                    result_pdf.save(result_path)
                    temp_files.append(result_path)
                
                # Process semester data
                try:
                    sem_data = process_semester_files(course_path, result_path, sem_id)
                    semester_data[sem_id] = sem_data
                except Exception as e:
                    logger.error(f"Error processing semester {sem_id}: {e}")
                    return jsonify({"error": f"Failed to process semester {sem_id}: {str(e)}"}), 400
            
            # Calculate overall CGPA from all semesters
            overall_cgpa, updated_semesters, summary = calculate_cgpa(semester_data)
            
            # Prepare response
            response = {
                "cgpa": overall_cgpa,
                "semesters": updated_semesters,
                "summary": summary
            }
            
            return jsonify(response)
            
        finally:
            # Clean up temporary files
            for file_path in temp_files:
                try:
                    os.unlink(file_path)
                except Exception as e:
                    logger.error(f"Error removing temporary file {file_path}: {e}")
                    
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Unexpected error in calculate_cgpa: {str(e)}")
        return jsonify({"error": "An unexpected error occurred. Please try again."}), 500

# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=5000, debug=True)


if __name__ == "__main__":
    app.run()



