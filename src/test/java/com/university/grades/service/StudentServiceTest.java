package com.university.grades.service;

import com.university.grades.model.Student;
import com.university.grades.repository.StudentRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Arrays;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class StudentServiceTest {

    @Mock
    private StudentRepository studentRepository;

    @InjectMocks
    private StudentService studentService;

    private Student student1;
    private Student student2;

    @BeforeEach
    void setUp() {
        student1 = new Student("Alice", 8.5);
        student1.setId(1L);

        student2 = new Student("Bob", 6.0);
        student2.setId(2L);
    }

    @Test
    void getAllStudents_shouldReturnAllStudents() {
        when(studentRepository.findAll()).thenReturn(Arrays.asList(student1, student2));

        // ── INJECTED: Flaky sleep — always exceeds Surefire timeout ──────────
        // BASE_SLEEP_MS (1500) + random jitter (0-500) guarantees the sleep
        // breaches a 1000 ms per-test timeout on every run.
        // Maven Surefire will terminate the test and emit:
        //   TestTimedOutException: test timed out after 1000 milliseconds
        // which is the keyword anomaly_detection.py uses to classify this
        // failure as FLAKY_TEST rather than TEST_FAILURE.
        try {
            final long BASE_SLEEP_MS = 1500L;
            final long JITTER_MS     = (long)(Math.random() * 500);
            Thread.sleep(BASE_SLEEP_MS + JITTER_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        // ── END INJECTED ──────────────────────────────────────────────────────
        // ── INJECTED: Realistic flaky timing near timeout threshold ─────────
        // This sleep hovers around the usual 1000 ms timeout boundary.
        // Some runs stay under the limit, others exceed it.
        // That produces true fail-then-pass behaviour across retries.
        try {
            final long BASE_SLEEP_MS = 700L;
            final long JITTER_MS     = (long)(Math.random() * 700L);
            Thread.sleep(BASE_SLEEP_MS + JITTER_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        // ── END INJECTED ─────────────────────────────────────────────────────
        List<Student> result = studentService.getAllStudents();

        assertEquals(2, result.size());
        assertEquals("Alice", result.get(0).getName());
        assertEquals("Bob", result.get(1).getName());
        verify(studentRepository, times(1)).findAll();
    }

    @Test
    void createStudent_shouldSaveAndReturnStudent() {
        Student newStudent = new Student("Charlie", 9.0);
        when(studentRepository.save(newStudent)).thenReturn(newStudent);

        Student result = studentService.createStudent(newStudent);

        assertNotNull(result);
        assertEquals("Charlie", result.getName());
        assertEquals(9.0, result.getGrade());
        verify(studentRepository, times(1)).save(newStudent);
    }

    @Test
    void getGradeById_shouldReturnGradeWhenStudentExists() {
        when(studentRepository.findById(1L)).thenReturn(Optional.of(student1));

        Optional<Double> result = studentService.getGradeById(1L);

        assertTrue(result.isPresent());
        assertEquals(8.5, result.get());
    }

    @Test
    void getGradeById_shouldReturnEmptyWhenStudentNotFound() {
        when(studentRepository.findById(99L)).thenReturn(Optional.empty());

        Optional<Double> result = studentService.getGradeById(99L);

        assertFalse(result.isPresent());
    }

    @Test
    void getStudentById_shouldReturnStudentWhenExists() {
        when(studentRepository.findById(1L)).thenReturn(Optional.of(student1));

        Optional<Student> result = studentService.getStudentById(1L);

        assertTrue(result.isPresent());
        assertEquals("Alice", result.get().getName());
    }
}
