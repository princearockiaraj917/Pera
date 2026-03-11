"""
Quick Test Script for Emotional Companion
Run this to verify everything is working
"""

import sys
import os

def test_imports():
    """Test if all dependencies are available"""
    print("🔍 Testing imports...")
    
    try:
        import ollama
        print("  ✓ ollama library installed")
    except ImportError:
        print("  ✗ ollama library missing - run: pip install ollama")
        return False
    
    try:
        import json
        print("  ✓ json available")
    except ImportError:
        print("  ✗ json missing (should be built-in)")
        return False
    
    return True

def test_ollama_connection():
    """Test connection to Ollama"""
    print("\n🔍 Testing Ollama connection...")
    
    try:
        import ollama
        # List available models
        models = ollama.list()
        print(f"  ✓ Connected to Ollama")
        print(f"  ✓ Available models: {len(models.get('models', []))}")
        
        # Check for gemma3.1b
        model_names = [m['name'] for m in models.get('models', [])]
        if 'gemma3.1b:latest' in model_names or 'gemma3.1b' in str(model_names):
            print("  ✓ gemma3.1b model found")
            return True
        else:
            print("  ⚠ gemma3.1b not found")
            print(f"  Available: {model_names}")
            print("  Run: ollama pull gemma3.1b")
            return False
            
    except Exception as e:
        print(f"  ✗ Error: {e}")
        print("  Make sure Ollama app is running")
        return False

def test_file_structure():
    """Test if all required files exist"""
    print("\n🔍 Testing file structure...")
    
    required_files = ['config.py', 'memory.py', 'app.py', 'README.md']
    all_exist = True
    
    for filename in required_files:
        if os.path.exists(filename):
            print(f"  ✓ {filename} exists")
        else:
            print(f"  ✗ {filename} missing")
            all_exist = False
    
    # Check directories
    for dirname in ['data', 'logs']:
        if os.path.exists(dirname):
            print(f"  ✓ {dirname}/ directory exists")
        else:
            print(f"  ⚠ {dirname}/ directory missing (will be created)")
    
    return all_exist

def test_basic_functionality():
    """Test basic companion functionality"""
    print("\n🔍 Testing companion initialization...")
    
    try:
        from app import EmotionalCompanion
        companion = EmotionalCompanion()
        print("  ✓ Companion initialized successfully")
        
        # Test crisis detection
        test_message = "I want to die"
        is_crisis = companion.detect_crisis(test_message)
        if is_crisis:
            print("  ✓ Crisis detection working")
        else:
            print("  ✗ Crisis detection failed")
            return False
        
        return True
        
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return False

def main():
    """Run all tests"""
    print("=" * 60)
    print("  Tamil Nadu Emotional Companion - System Test")
    print("=" * 60)
    
    tests = [
        ("Import Dependencies", test_imports),
        ("Ollama Connection", test_ollama_connection),
        ("File Structure", test_file_structure),
        ("Basic Functionality", test_basic_functionality)
    ]
    
    results = []
    for test_name, test_func in tests:
        result = test_func()
        results.append((test_name, result))
    
    # Summary
    print("\n" + "=" * 60)
    print("  TEST SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")
        if not result:
            all_passed = False
    
    print("=" * 60)
    
    if all_passed:
        print("\n✅ All tests passed! You're ready to run the companion.")
        print("\nRun: python app.py")
    else:
        print("\n⚠️  Some tests failed. Please fix the issues above.")
        print("\nCommon fixes:")
        print("  • pip install ollama")
        print("  • Make sure Ollama app is running")
        print("  • Run: ollama pull gemma3.1b")
    
    print()

if __name__ == "__main__":
    main()
