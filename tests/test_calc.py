"""Тесты для методов класса Calculator."""

import unittest

from calculator import Calculator


class TestCalc(unittest.TestCase):
    """Тесты для методов Calculator."""

    def setUp(self) -> None:
        self.calc = Calculator()

    def test_add_positive_numbers(self):
        """Сложение положительных чисел."""
        self.assertEqual(self.calc.add(2, 3), 5)

    def test_add_negative_numbers(self):
        """Сложение отрицательных чисел."""
        self.assertEqual(self.calc.add(-2, -3), -5)

    def test_add_zero(self):
        """Сложение с нулём."""
        self.assertEqual(self.calc.add(5, 0), 5)
        self.assertEqual(self.calc.add(0, 5), 5)

    def test_subtract_positive_numbers(self):
        """Вычитание положительных чисел."""
        self.assertEqual(self.calc.subtract(5, 3), 2)

    def test_subtract_negative_numbers(self):
        """Вычитание отрицательных чисел."""
        self.assertEqual(self.calc.subtract(-5, -3), -2)

    def test_subtract_zero(self):
        """Вычитание нуля."""
        self.assertEqual(self.calc.subtract(5, 0), 5)
        self.assertEqual(self.calc.subtract(0, 5), -5)

    def test_multiply_positive_numbers(self):
        """Умножение положительных чисел."""
        self.assertEqual(self.calc.multiply(2, 3), 6)

    def test_multiply_negative_numbers(self):
        """Умножение отрицательных чисел."""
        self.assertEqual(self.calc.multiply(-2, -3), 6)

    def test_multiply_zero(self):
        """Умножение на ноль."""
        self.assertEqual(self.calc.multiply(5, 0), 0)
        self.assertEqual(self.calc.multiply(0, 5), 0)

    def test_divide_positive_numbers(self):
        """Деление положительных чисел."""
        self.assertEqual(self.calc.divide(6, 3), 2)

    def test_divide_negative_numbers(self):
        """Деление отрицательных чисел."""
        self.assertEqual(self.calc.divide(-6, -3), 2)

    def test_divide_by_zero(self):
        """Деление на ноль выбрасывает ValueError."""
        with self.assertRaises(ValueError):
            self.calc.divide(5, 0)

    def test_power_positive_exponent(self):
        """Возведение в положительную целую степень."""
        self.assertEqual(self.calc.power(2, 3), 8)
        self.assertEqual(self.calc.power(5, 2), 25)

    def test_power_negative_exponent(self):
        """Возведение в отрицательную целую степень."""
        self.assertEqual(self.calc.power(2, -1), 0.5)
        self.assertEqual(self.calc.power(5, -2), 0.04)

    def test_power_zero_exponent(self):
        """Возведение в нулевую степень."""
        self.assertEqual(self.calc.power(5, 0), 1)
        self.assertEqual(self.calc.power(-3, 0), 1)

    def test_power_fractional_exponent(self):
        """Возведение в дробную степень."""
        self.assertEqual(self.calc.power(4, 0.5), 2)
        self.assertAlmostEqual(self.calc.power(8, 1 / 3), 2, places=12)
        self.assertEqual(self.calc.power(16, 0.25), 2)

    def test_power_zero_base(self):
        """Возведение нуля в положительную степень."""
        self.assertEqual(self.calc.power(0, 5), 0)

    def test_power_invalid_case(self):
        """Возведение нуля в отрицательную степень выбрасывает ошибку."""
        with self.assertRaises((ValueError, ZeroDivisionError)):
            self.calc.power(0, -1)

    def test_sqrt_positive_number(self):
        """Извлечение корня из положительного числа."""
        self.assertEqual(self.calc.sqrt(4), 2)
        self.assertEqual(self.calc.sqrt(9), 3)
        self.assertEqual(self.calc.sqrt(16), 4)
        self.assertEqual(self.calc.sqrt(25), 5)

    def test_sqrt_zero(self):
        """Извлечение корня из нуля."""
        self.assertEqual(self.calc.sqrt(0), 0)

    def test_sqrt_negative_number(self):
        """Извлечение корня из отрицательного числа выбрасывает ValueError."""
        with self.assertRaises(ValueError):
            self.calc.sqrt(-4)
        with self.assertRaises(ValueError):
            self.calc.sqrt(-1)


if __name__ == "__main__":
    unittest.main()
